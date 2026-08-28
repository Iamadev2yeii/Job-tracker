"""
Entry point for the scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml,
runs daily at 06:30 Munich time, plus manual "Run workflow" trigger).

Two scrape pools, three sheets:

  POOL A — Munich (config/companies.yaml + general_roles_companies.yaml
  + bavaria_directory_companies.yaml + arbeitsagentur_searches.yaml —
  deliberately NOT esg_job_boards.yaml; the "Jobs" sheet is picked
  directly from company career pages or the Bundesagentur API's real
  employer data, not job boards/agencies). Scraped ONCE per company,
  then run through TWO independent matching pipelines:
    1. Sustainability/ESG (config/cv_profile.yaml) -> "Jobs" sheet.
    2. Internships/traineeships, any field
       (config/general_roles_profile.yaml) -> "Internships &
       Traineeships (Munich)" sheet. Deliberately broad on field
       (not just sustainability, not just office/admin) and narrow on
       type (Praktikum/Trainee only, no Werkstudent, no permanent
       roles) — still scored against Prabha's CV themes for ranking.

  POOL B — Remote-Europe (config/remote_sustainability_companies.yaml,
  a small set of dedicated climate/ESG job boards). Scraped
  separately, matched against config/remote_sustainability_profile.yaml
  and confirmed remote-and-Europe-eligible (not Munich-area) ->
  "Remote Sustainability (Europe)" sheet, with a small scoring boost
  for junior/entry-level titles.

Each pipeline works on its OWN COPY of every scraped job (see
_process_profile) so enriching/scoring one pipeline never leaks into
another, even for a posting that happens to satisfy more than one
title list.

No score floor on any of the three sheets — main_min_score in all
three profile files defaults to 0.
"""

from __future__ import annotations

import copy
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ats_scrapers import scrape_company, fetch_description_fallback, reset_headless_budget
from src.matcher import (
    filter_by_title_only, filter_internships, resolve_munich_match, resolve_remote_eu_match,
    resolve_berlin_match, extract_location_snippet, extract_remote_snippet, extract_berlin_snippet,
    score_jobs, is_excluded_seniority, is_excluded_language,
)
from src.tracker import update_tracker, update_general_tracker, update_remote_tracker, update_berlin_tracker
from src.generate_html import generate as generate_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("job_scraper.main")

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.yaml"
ESG_JOB_BOARDS_FILE = ROOT / "config" / "esg_job_boards.yaml"
GENERAL_ROLES_COMPANIES_FILE = ROOT / "config" / "general_roles_companies.yaml"
BAVARIA_DIRECTORY_COMPANIES_FILE = ROOT / "config" / "bavaria_directory_companies.yaml"
ARBEITSAGENTUR_SEARCHES_FILE = ROOT / "config" / "arbeitsagentur_searches.yaml"
REMOTE_SUSTAINABILITY_COMPANIES_FILE = ROOT / "config" / "remote_sustainability_companies.yaml"
BERLIN_COMPANIES_FILE = ROOT / "config" / "berlin_companies.yaml"
BERLIN_ARBEITSAGENTUR_SEARCHES_FILE = ROOT / "config" / "berlin_arbeitsagentur_searches.yaml"
CV_PROFILE_FILE = ROOT / "config" / "cv_profile.yaml"
GENERAL_ROLES_PROFILE_FILE = ROOT / "config" / "general_roles_profile.yaml"
REMOTE_SUSTAINABILITY_PROFILE_FILE = ROOT / "config" / "remote_sustainability_profile.yaml"
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"

# Concurrent scraping — see scrape_munich_pool's docstring for the
# reasoning behind this specific number. 10 is a deliberate middle
# ground: fast enough to meaningfully cut a 30-40 minute sequential
# run down, without hammering shared platform endpoints (Greenhouse,
# Lever, Personio) as hard as a much higher number would.
MUNICH_POOL_WORKERS = 10
BERLIN_POOL_WORKERS = 10


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _process_profile(
    raw_jobs: list[dict[str, Any]],
    profile: dict[str, Any],
    company_name: str,
    resolver: Callable[..., bool],
    resolver_kwargs: dict[str, Any],
    log_diag: bool,
    junior_priority: bool = False,
    snippet_fn: Callable[[str], str] = extract_location_snippet,
    assumed_location_text: str = "Munich (assumed — single-office company)",
    title_filter: Callable[..., list[dict[str, Any]]] = filter_by_title_only,
    title_filter_kwargs: dict[str, Any] | None = None,
    skip_relevance_filter: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Runs one company's raw scrape through one profile's title filter,
    a location/remote resolver, and scoring. Operates on fresh copies
    of every job so this pipeline's enrichment/scoring never
    contaminates another pipeline sharing the same raw_jobs list.

    resolver is resolve_munich_match or resolve_remote_eu_match;
    resolver_kwargs carries whatever extra args that resolver needs
    (e.g. assume_local for the Munich one). snippet_fn and
    assumed_location_text let the remote-Europe pipeline use its own
    "Remote"-flavored wording instead of the Munich-specific defaults
    — using the Munich snippet function for the remote pipeline was a
    real bug (its fallback literally returns the string "Munich"),
    which is why remote-sheet postings kept showing "Munich" as their
    location even though the whole point of that sheet is that Munich
    isn't where they are. title_filter/title_filter_kwargs let the
    internships pipeline use filter_internships (title + relevance,
    with a per-company exemption) instead of the plain title check.

    skip_relevance_filter: for sources that already did their own
    keyword-relevance filtering server-side (the Bundesagentur API's
    was=<keyword> searches — see arbeitsagentur_searches.yaml,
    assume_relevant_title), re-applying a literal title-substring
    check on top is often wrong: the API's own free-text search
    matches thematically, not by literal substring, so a job it
    correctly returned for "was=Nachhaltigkeit" won't necessarily have
    that word anywhere in its own title. When set, the title_must_match
    gate is skipped entirely (trusting the source), but seniority and
    language exclusion still apply — a search source doing its own
    relevance filtering is not the same as it doing seniority/language
    filtering, which stay this project's own job regardless of source.
    """
    jobs = [copy.deepcopy(j) for j in raw_jobs]
    if skip_relevance_filter:
        title_matched_jobs = [
            job for job in jobs
            if job.get("title")
            and not is_excluded_seniority(job["title"])
            and not is_excluded_language(job["title"])
        ]
    else:
        title_matched_jobs = title_filter(jobs, profile, **(title_filter_kwargs or {}))

    if not title_matched_jobs and log_diag:
        sample_titles = [j.get("title", "") for j in jobs[:8] if j.get("title")]
        logger.info("  DIAG: no title match. Sample raw titles seen: %s", sample_titles)

    matched = []
    for job in title_matched_jobs:
        enrichment_text = None
        if not job.get("location") and job.get("url"):
            enrichment_text = fetch_description_fallback(job["url"])

        search_text = enrichment_text if enrichment_text is not None else job.get("location", "")
        if not resolver(job, search_text=search_text, **resolver_kwargs):
            continue

        if enrichment_text and not job.get("location"):
            job["location"] = snippet_fn(enrichment_text)
        if not job.get("location") and resolver_kwargs.get("assume_local"):
            job["location"] = assumed_location_text

        if not job.get("description") and enrichment_text:
            job["description"] = enrichment_text
        elif not job.get("description") and job.get("url"):
            job["description"] = fetch_description_fallback(job["url"])

        matched.append(job)

    score_jobs(matched, profile, junior_priority=junior_priority)
    for job in matched:
        # Most scrapers don't set "company" themselves, so this fills
        # it in from the configured entry's display name. The
        # Bundesagentur API is a genuine exception — it returns the
        # REAL hiring employer for each posting, which is strictly
        # better than the search entry's own name ("Bundesagentur für
        # Arbeit (Nachhaltigkeit, München)") — so that's preserved
        # instead of being overwritten.
        if not job.get("company"):
            job["company"] = company_name

    stats = {"title_matched": len(title_matched_jobs), "location_confirmed": len(matched)}
    return matched, stats


def scrape_munich_pool(
    companies: list[dict], cv_profile: dict, general_profile: dict
) -> tuple[list[dict], list[dict], dict]:
    """Scrapes every Munich-pool company once, then runs both the
    sustainability and general-roles pipelines over that single
    scrape. Returns (sustainability_jobs, general_jobs, counts).

    Concurrent (MUNICH_POOL_WORKERS companies in flight at once) —
    this used to be a strict one-at-a-time loop, which is why a full
    run took 30-40 minutes for ~388 sources. Each company hits its OWN
    domain, so the main risk isn't hammering one server — it's the
    handful of SHARED platform endpoints (Greenhouse, Lever, Personio)
    that many different companies' checks all go through, which could
    see stricter rate-limiting under concurrency than one-at-a-time
    did. A modest worker count is a deliberate compromise between
    "much faster" and "not meaningfully harsher on those shared
    endpoints than before" — raise MUNICH_POOL_WORKERS if that
    trade-off looks safe in practice, lower it if 429s get noticeably
    worse.
    """
    sustainability_jobs: list[dict[str, Any]] = []
    general_jobs: list[dict[str, Any]] = []
    ok_count = 0
    empty_count = 0
    error_count = 0
    total_title_matched = 0
    total_confirmed = 0

    def _work(company: dict) -> tuple[str, list[dict], list[dict], dict, str]:
        """Runs entirely inside a worker thread: scrape + both
        pipelines for one company. Returns everything the main thread
        needs to log and accumulate — no shared state touched here."""
        name = company["name"]
        try:
            raw_jobs = scrape_company(company)
        except Exception as exc:  # extra safety net at the orchestration level
            return name, [], [], {}, f"FAILED:{exc}"

        if not raw_jobs:
            return name, [], [], {}, "EMPTY"

        assume_local = bool(company.get("assume_local", False))
        sustainability_related_company = bool(company.get("sustainability_related", False))
        assume_relevant_title = bool(company.get("assume_relevant_title", False))

        sustain_matched, sustain_stats = _process_profile(
            raw_jobs, cv_profile, name, resolve_munich_match, {"assume_local": assume_local}, log_diag=True,
            junior_priority=True, skip_relevance_filter=assume_relevant_title,
        )
        general_matched, general_stats = _process_profile(
            raw_jobs, general_profile, name, resolve_munich_match, {"assume_local": assume_local}, log_diag=False,
            title_filter=filter_internships,
            title_filter_kwargs={"sustainability_related_company": sustainability_related_company},
        )
        stats = {
            "postings": len(raw_jobs),
            "sustain_title_matched": sustain_stats["title_matched"],
            "sustain_confirmed": sustain_stats["location_confirmed"],
            "general_title_matched": general_stats["title_matched"],
            "general_confirmed": general_stats["location_confirmed"],
        }
        return name, sustain_matched, general_matched, stats, "OK"

    with ThreadPoolExecutor(max_workers=MUNICH_POOL_WORKERS) as pool:
        futures = {pool.submit(_work, company): company for company in companies}
        for future in as_completed(futures):
            name, sustain_matched, general_matched, stats, status = future.result()

            if status == "EMPTY":
                logger.info("EMPTY   %-35s (no postings found / scraper returned nothing)", name)
                empty_count += 1
                continue
            if status.startswith("FAILED"):
                logger.error("FAILED  %-35s %s", name, status.split(":", 1)[1])
                error_count += 1
                continue

            sustainability_jobs.extend(sustain_matched)
            general_jobs.extend(general_matched)
            logger.info(
                "OK      %-35s %3d postings, %2d ESG-matched (%2d confirmed), %2d general-matched (%2d confirmed)",
                name, stats["postings"], stats["sustain_title_matched"], stats["sustain_confirmed"],
                stats["general_title_matched"], stats["general_confirmed"],
            )
            total_title_matched += stats["sustain_title_matched"] + stats["general_title_matched"]
            total_confirmed += stats["sustain_confirmed"] + stats["general_confirmed"]
            ok_count += 1

    counts = {
        "ok": ok_count, "empty": empty_count, "errored": error_count, "total": len(companies),
        "title_matched": total_title_matched, "location_confirmed": total_confirmed,
    }
    return sustainability_jobs, general_jobs, counts


def scrape_berlin_pool(companies: list[dict], cv_profile: dict) -> tuple[list[dict], dict]:
    """
    Berlin equivalent of scrape_munich_pool — but simpler, since
    there's only one sheet ("Jobs - Berlin") for Berlin, not also an
    internships/traineeships one. Same reuse of cv_profile.yaml (same
    sustainability field, just a different city's location check —
    resolve_berlin_match instead of resolve_munich_match) and the same
    concurrent-scraping approach for the same reason (this pool is
    much smaller than the Munich one, but no reason not to be
    consistent).
    """
    berlin_jobs: list[dict[str, Any]] = []
    ok_count = 0
    empty_count = 0
    error_count = 0
    total_title_matched = 0
    total_confirmed = 0

    def _work(company: dict) -> tuple[str, list[dict], dict, str]:
        name = company["name"]
        try:
            raw_jobs = scrape_company(company)
        except Exception as exc:
            return name, [], {}, f"FAILED:{exc}"

        if not raw_jobs:
            return name, [], {}, "EMPTY"

        assume_local = bool(company.get("assume_local", False))
        assume_relevant_title = bool(company.get("assume_relevant_title", False))

        matched, stats = _process_profile(
            raw_jobs, cv_profile, name, resolve_berlin_match, {"assume_local": assume_local}, log_diag=True,
            junior_priority=True, snippet_fn=extract_berlin_snippet,
            assumed_location_text="Berlin (assumed — single-office company)",
            skip_relevance_filter=assume_relevant_title,
        )
        stats["postings"] = len(raw_jobs)
        return name, matched, stats, "OK"

    with ThreadPoolExecutor(max_workers=BERLIN_POOL_WORKERS) as pool:
        futures = {pool.submit(_work, company): company for company in companies}
        for future in as_completed(futures):
            name, matched, stats, status = future.result()

            if status == "EMPTY":
                logger.info("EMPTY   %-35s (no postings found / scraper returned nothing)", name)
                empty_count += 1
                continue
            if status.startswith("FAILED"):
                logger.error("FAILED  %-35s %s", name, status.split(":", 1)[1])
                error_count += 1
                continue

            berlin_jobs.extend(matched)
            logger.info(
                "OK      %-35s %3d postings, %2d title-matched, %2d confirmed Berlin",
                name, stats["postings"], stats["title_matched"], stats["location_confirmed"],
            )
            total_title_matched += stats["title_matched"]
            total_confirmed += stats["location_confirmed"]
            ok_count += 1

    counts = {
        "ok": ok_count, "empty": empty_count, "errored": error_count, "total": len(companies),
        "title_matched": total_title_matched, "location_confirmed": total_confirmed,
    }
    return berlin_jobs, counts


def scrape_remote_pool(companies: list[dict], remote_profile: dict) -> tuple[list[dict], dict]:
    """Scrapes the remote-Europe job boards and matches against
    remote_sustainability_profile.yaml, with junior-title priority."""
    remote_jobs: list[dict[str, Any]] = []
    ok_count = 0
    empty_count = 0
    error_count = 0
    total_title_matched = 0
    total_confirmed = 0

    for company in companies:
        name = company["name"]
        try:
            raw_jobs = scrape_company(company)
        except Exception as exc:
            logger.error("FAILED  %-35s %s", name, exc)
            error_count += 1
            continue

        if not raw_jobs:
            logger.info("EMPTY   %-35s (no postings found / scraper returned nothing)", name)
            empty_count += 1
            continue

        matched, stats = _process_profile(
            raw_jobs, remote_profile, name, resolve_remote_eu_match, {}, log_diag=True, junior_priority=True,
            snippet_fn=extract_remote_snippet, assumed_location_text="Remote (Europe)",
        )
        remote_jobs.extend(matched)

        logger.info(
            "OK      %-35s %3d postings, %2d title-matched, %2d confirmed remote/EU",
            name, len(raw_jobs), stats["title_matched"], stats["location_confirmed"],
        )
        total_title_matched += stats["title_matched"]
        total_confirmed += stats["location_confirmed"]
        ok_count += 1
        time.sleep(0.3)

    counts = {
        "ok": ok_count, "empty": empty_count, "errored": error_count, "total": len(companies),
        "title_matched": total_title_matched, "location_confirmed": total_confirmed,
    }
    return remote_jobs, counts


def run() -> None:
    cv_profile = load_yaml(CV_PROFILE_FILE)
    general_profile = load_yaml(GENERAL_ROLES_PROFILE_FILE)
    remote_profile = load_yaml(REMOTE_SUSTAINABILITY_PROFILE_FILE)
    reset_headless_budget()  # one shared 15-min headless budget for the whole run

    companies = load_yaml(COMPANIES_FILE)["companies"]
    # esg_job_boards.yaml (NachhaltigeJobs.de, ClimateTechList, and the
    # staffing-agency search pages) is deliberately NOT loaded into the
    # Munich pool anymore — per request, the "Jobs" sheet should only
    # ever surface postings picked up directly from a specific
    # company's own career page (or the Bundesagentur API below, which
    # returns the real hiring employer for each posting — the same
    # spirit, just a different mechanism). The file itself is left in
    # place in case it's wanted back later.
    if GENERAL_ROLES_COMPANIES_FILE.exists():
        companies = companies + load_yaml(GENERAL_ROLES_COMPANIES_FILE)["companies"]
    if BAVARIA_DIRECTORY_COMPANIES_FILE.exists():
        companies = companies + load_yaml(BAVARIA_DIRECTORY_COMPANIES_FILE)["companies"]
    if ARBEITSAGENTUR_SEARCHES_FILE.exists():
        companies = companies + load_yaml(ARBEITSAGENTUR_SEARCHES_FILE)["companies"]

    sustainability_jobs, general_jobs, munich_counts = scrape_munich_pool(companies, cv_profile, general_profile)

    berlin_companies = []
    if BERLIN_COMPANIES_FILE.exists():
        berlin_companies = berlin_companies + load_yaml(BERLIN_COMPANIES_FILE)["companies"]
    if BERLIN_ARBEITSAGENTUR_SEARCHES_FILE.exists():
        berlin_companies = berlin_companies + load_yaml(BERLIN_ARBEITSAGENTUR_SEARCHES_FILE)["companies"]
    berlin_jobs, berlin_counts = scrape_berlin_pool(berlin_companies, cv_profile)

    remote_companies = []
    if REMOTE_SUSTAINABILITY_COMPANIES_FILE.exists():
        remote_companies = load_yaml(REMOTE_SUSTAINABILITY_COMPANIES_FILE)["companies"]
    remote_jobs, remote_counts = scrape_remote_pool(remote_companies, remote_profile)

    summary = update_tracker(TRACKER_FILE, sustainability_jobs, min_score=cv_profile.get("main_min_score", 0))
    general_summary = update_general_tracker(
        TRACKER_FILE, general_jobs, min_score=general_profile.get("main_min_score", 0)
    )
    remote_summary = update_remote_tracker(
        TRACKER_FILE, remote_jobs, min_score=remote_profile.get("main_min_score", 0)
    )
    berlin_summary = update_berlin_tracker(TRACKER_FILE, berlin_jobs, min_score=cv_profile.get("main_min_score", 0))

    logger.info("-" * 60)
    logger.info(
        "Munich pool: %d ok / %d empty / %d errored (of %d total)",
        munich_counts["ok"], munich_counts["empty"], munich_counts["errored"], munich_counts["total"],
    )
    logger.info(
        "Berlin pool: %d ok / %d empty / %d errored (of %d total)",
        berlin_counts["ok"], berlin_counts["empty"], berlin_counts["errored"], berlin_counts["total"],
    )
    logger.info(
        "Remote pool: %d ok / %d empty / %d errored (of %d total)",
        remote_counts["ok"], remote_counts["empty"], remote_counts["errored"], remote_counts["total"],
    )
    logger.info(
        "Jobs sheet (Munich sustainability): %d new rows added, %d already tracked, %d pruned, %d total rows",
        summary["added"], summary["already_tracked"], summary["pruned"], summary["total_rows"],
    )
    logger.info(
        "Munich Internships & Trainee sheet: %d new rows added, %d already tracked, %d pruned, %d total rows",
        general_summary["added"], general_summary["already_tracked"], general_summary["pruned"],
        general_summary["total_rows"],
    )
    logger.info(
        "Remote Sustainability (Europe) sheet: %d new rows added, %d already tracked, %d pruned, %d total rows",
        remote_summary["added"], remote_summary["already_tracked"], remote_summary["pruned"],
        remote_summary["total_rows"],
    )
    logger.info(
        "Jobs - Berlin sheet: %d new rows added, %d already tracked, %d pruned, %d total rows",
        berlin_summary["added"], berlin_summary["already_tracked"], berlin_summary["pruned"],
        berlin_summary["total_rows"],
    )

    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
