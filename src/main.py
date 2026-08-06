"""
Entry point for the scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml,
runs daily at 06:30 Munich time, plus manual "Run workflow" trigger).

Single-city version: scrapes every company in config/companies.yaml
(all Munich-area career pages) and keeps only postings that are BOTH
title-matched (ESG/sustainability/carbon roles per cv_profile.yaml)
AND confirmed to be in the Munich metro area. Everything feeds one
tracker sheet — no Dream Cities, no other tiers.

No score floor — every job that passes the title + confirmed-location
filter is kept and shown.

Pipeline per company: scrape -> filter by title -> enrich description
for survivors that don't have a location yet (fetches the job's own
posting page, see ats_scrapers.fetch_description_fallback) -> confirm
Munich match -> score.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ats_scrapers import scrape_company, fetch_description_fallback, reset_headless_budget
from src.matcher import (
    filter_by_title_only, resolve_munich_match, extract_location_snippet, score_jobs,
)
from src.tracker import update_tracker
from src.generate_html import generate as generate_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("job_scraper.main")

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.yaml"
CV_PROFILE_FILE = ROOT / "config" / "cv_profile.yaml"
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def scrape_all_munich(companies: list[dict], cv_profile: dict) -> tuple[list[dict], dict]:
    """
    Scrapes every company once, filters by title, and confirms each
    title-matched posting is actually in the Munich metro area (not
    just wherever the company's config entry happens to be tagged —
    every company here is already tagged Munich, but a company's job
    board can still return postings from other cities entirely, e.g.
    a global corporate board).

    Same enrich-before-filtering ordering fix as Giorgio's original
    tracker: scrape_generic (ats_scrapers.py) always leaves location
    blank, so for title-matched postings with no location yet, the
    individual posting page is fetched FIRST and used as the search
    text for the Munich decision — otherwise the location filter would
    reject every generic-scraped posting before it ever had a chance
    to have its real location discovered. That same fetched text is
    reused as the description too, so no posting gets fetched twice.
    """
    munich_jobs = []
    ok_count = 0
    empty_count = 0
    error_count = 0
    total_title_matched = 0
    total_confirmed = 0

    for company in companies:
        name = company["name"]
        try:
            raw_jobs = scrape_company(company)
        except Exception as exc:  # extra safety net at the orchestration level
            logger.error("FAILED  %-35s %s", name, exc)
            error_count += 1
            continue

        if not raw_jobs:
            logger.info("EMPTY   %-35s (no postings found / scraper returned nothing)", name)
            empty_count += 1
            continue

        title_matched_jobs = filter_by_title_only(raw_jobs, cv_profile)

        matched = []
        for job in title_matched_jobs:
            enrichment_text = None
            if not job.get("location") and job.get("url"):
                enrichment_text = fetch_description_fallback(job["url"])

            search_text = enrichment_text if enrichment_text is not None else job.get("location", "")
            if not resolve_munich_match(job, search_text=search_text):
                continue

            if enrichment_text and not job.get("location"):
                job["location"] = extract_location_snippet(enrichment_text)

            if not job.get("description") and enrichment_text:
                job["description"] = enrichment_text
            elif not job.get("description") and job.get("url"):
                job["description"] = fetch_description_fallback(job["url"])

            matched.append(job)

        stats = {"title_matched": len(title_matched_jobs), "location_confirmed": len(matched)}

        if stats["title_matched"] > 0 and stats["location_confirmed"] == 0:
            for j in title_matched_jobs[:5]:
                logger.info(
                    "  DIAG: title=%r  raw_location=%r  (Munich area not confirmed, even after enrichment)",
                    j.get("title", ""), (j.get("location", "") or "")[:200],
                )

        score_jobs(matched, cv_profile)

        for job in matched:
            job["company"] = name
            munich_jobs.append(job)

        logger.info(
            "OK      %-35s %3d postings, %2d title-matched, %2d confirmed",
            name, len(raw_jobs), stats["title_matched"], stats["location_confirmed"],
        )
        total_title_matched += stats["title_matched"]
        total_confirmed += stats["location_confirmed"]
        ok_count += 1
        time.sleep(0.3)  # be polite to career-page servers

    counts = {
        "ok": ok_count,
        "empty": empty_count,
        "errored": error_count,
        "total": len(companies),
        "title_matched": total_title_matched,
        "location_confirmed": total_confirmed,
    }
    return munich_jobs, counts


def run() -> None:
    cv_profile = load_yaml(CV_PROFILE_FILE)
    reset_headless_budget()  # one shared 15-min headless budget for the whole run

    companies = load_yaml(COMPANIES_FILE)["companies"]
    munich_jobs, counts = scrape_all_munich(companies, cv_profile)

    summary = update_tracker(TRACKER_FILE, munich_jobs, min_score=cv_profile.get("main_min_score", 0))

    logger.info("-" * 60)
    logger.info(
        "Scrape: %d ok / %d empty / %d errored (of %d total)",
        counts["ok"], counts["empty"], counts["errored"], counts["total"],
    )
    logger.info(
        "%d total title-matched, %d total confirmed in the Munich area",
        counts["title_matched"], counts["location_confirmed"],
    )
    logger.info(
        "Jobs sheet: %d new rows added, %d already tracked, %d pruned, %d total rows",
        summary["added"], summary["already_tracked"], summary["pruned"], summary["total_rows"],
    )

    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
