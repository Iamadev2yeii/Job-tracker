"""
Filters and scores scraped jobs against config/cv_profile.yaml.

Simplified single-city version of Giorgio's original multi-tier
matcher: this tracker only ever checks one area — Greater Munich —
so there's no "any approved city" routing, no Dream Cities, no
Singapore/Swiss tier. One filter, one sheet.

Two-step pipeline (split so main.py can enrich description text in
between — see src/ats_scrapers.py, fetch_description_fallback):

  1. filter_by_title_only(jobs, cv_profile):
     - KEEPS a job only if its title matches one of `title_must_match`.
     - Does NOT check location yet, and does NOT score yet.

  2. resolve_munich_match(job, search_text=None):
     - Checks a title-matched job against the Munich metro area
       (Munich itself, plus commuter-belt satellite towns — Ottobrunn,
       Freising, Garching, etc.). Returns True/False. This matters
       because several platforms (Lever, Greenhouse, SmartRecruiters,
       Workday) return a company's ENTIRE global job board in one
       call, so a Munich-tagged company's board can include a Berlin
       or Abu Dhabi posting alongside genuinely local ones.

  3. score_jobs(jobs, cv_profile):
     - Adds `relevance_score` (1-10 scale, 10 = best match to your CV)
       to each job. Does not filter anything out by score — every job
       that passed step 2 is kept and shown, regardless of score.

No score threshold is applied anywhere in this file — main_min_score
in cv_profile.yaml defaults to 0 (no floor), reflecting that a
title+location match is considered worth seeing regardless of how
strong the keyword match is.
"""

from __future__ import annotations

from typing import Any

DEFAULT_SCORE_CEILING = 12

# Keywords that count as "this location IS in the Munich metro area" —
# the city itself plus its commuter-belt satellite towns, since a job
# in Ottobrunn or Freising is just as reachable/relevant as one in
# Munich proper. Deliberately does NOT extend to all of Bavaria —
# Nuremberg, Augsburg, Regensburg, etc. are real cities in their own
# right, hours away, not "the Munich area." Edit this list directly if
# you want a specific town added or removed.
MUNICH_KEYWORDS = [
    "munich", "münchen", "muenchen",
    "ottobrunn", "taufkirchen", "manching", "garching",
    "oberpfaffenhofen", "unterschleissheim", "unterschleißheim",
    "ismaning", "unterföhring", "unterfoehring", "neubiberg",
    "poing", "feldkirchen", "holzkirchen", "dachau", "freising",
    "erding", "fürstenfeldbruck", "fuerstenfeldbruck", "starnberg",
    "germering", "gräfelfing", "graefelfing", "planegg", "gilching",
    "puchheim", "vaterstetten", "haar", "aschheim", "kirchheim",
]

# Keywords that name a clearly different city, so a location string
# that also happens to mention "Germany" in passing isn't treated as
# a Munich match. A location too vague to confirm at all (blank, or
# just "Germany (Remote)") is "unconfirmed," not excluded — only a
# NAMED other city triggers exclusion.
OTHER_MAJOR_LOCATIONS = [
    "berlin", "hamburg", "frankfurt", "cologne", "köln", "stuttgart",
    "düsseldorf", "duesseldorf", "leipzig", "dresden", "nuremberg",
    "nürnberg", "augsburg", "regensburg", "würzburg", "wuerzburg",
    "ingolstadt", "hanover", "hannover", "bremen", "essen", "dortmund",
    "new york", "san francisco", "london", "paris", "warsaw", "krakow",
    "dublin", "amsterdam", "madrid", "barcelona", "lisbon", "milan",
    "vienna", "prague", "budapest", "singapore", "tokyo", "bangalore",
    "hyderabad", "delhi", "mumbai", "toronto", "seattle", "austin",
    "boston", "chicago", "los angeles", "washington", "atlanta",
    "zurich", "zürich", "geneva", "genève", "basel", "bern", "lausanne",
    "lucerne", "luzern",
]


def title_matches(title: str, must_match: list[str]) -> bool:
    t = title.lower()
    return any(term.lower() in t for term in must_match)


def location_status(location: str) -> str:
    """
    Returns "confirmed" (location text names the Munich metro area),
    "mismatch" (location text clearly names a different city), or
    "unconfirmed" (no location text available, or it's too generic to
    tell — e.g. just "Germany" or "Remote").
    """
    if not location:
        return "unconfirmed"
    loc = location.lower()
    if any(kw in loc for kw in MUNICH_KEYWORDS):
        return "confirmed"
    if any(kw in loc for kw in OTHER_MAJOR_LOCATIONS):
        return "mismatch"
    return "unconfirmed"


def filter_by_title_only(jobs: list[dict[str, Any]], cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Just the title check, no location decision — exists so main.py can
    enrich blank-location postings BEFORE the location filter runs,
    rather than after (see main.py's scrape_munich docstring for why
    this ordering matters).
    """
    must_match = cv_profile.get("title_must_match", [])
    return [job for job in jobs if job.get("title") and title_matches(job["title"], must_match)]


def resolve_munich_match(job: dict[str, Any], search_text: str | None = None) -> bool:
    """
    Decides whether a single already title-matched job is confirmed
    in the Munich metro area. Searches search_text if given (e.g. an
    enriched full-page text blob), otherwise falls back to
    job["location"]. Only "confirmed" counts — "unconfirmed" and
    "mismatch" are both dropped, same as Giorgio's original tracker
    (no "unconfirmed location" jobs are shown).
    """
    text = search_text if search_text is not None else job.get("location", "")
    return location_status(text) == "confirmed"


def extract_location_snippet(text: str, window: int = 30) -> str:
    """
    For a job whose location was recovered from a full page-text blob
    (see main.py's enrichment step) rather than a clean structured
    field, pulls a short window of text around wherever a Munich
    keyword was actually found — so the tracker's Location column
    shows something readable instead of the entire fetched page.
    Falls back to "Munich" if no keyword position is found (shouldn't
    happen if this text really matched, but defensive regardless).
    """
    loc = text.lower()
    for kw in MUNICH_KEYWORDS:
        idx = loc.find(kw)
        if idx != -1:
            start = max(0, idx - window)
            end = min(len(text), idx + len(kw) + window)
            snippet = text[start:end].strip()
            return ("..." if start > 0 else "") + snippet + ("..." if end < len(text) else "")
    return "Munich"


def _raw_score(description: str, title: str, keywords: list[dict[str, Any]]) -> int:
    text = f"{title} {description}".lower()
    score = 0
    for kw in keywords:
        if kw["term"].lower() in text:
            score += int(kw["weight"])
    return score


def score_job_1_to_10(description: str, title: str, keywords: list[dict[str, Any]], ceiling: int) -> int:
    raw = _raw_score(description, title, keywords)
    scaled = round((raw / ceiling) * 10) if ceiling else 0
    return max(1, min(10, scaled))


def score_jobs(jobs: list[dict[str, Any]], cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Adds relevance_score to every job in place. Does not filter
    anything out — score is for ranking/sorting only."""
    keywords = cv_profile.get("scoring_keywords", [])
    ceiling = cv_profile.get("score_ceiling", DEFAULT_SCORE_CEILING)
    for job in jobs:
        job["relevance_score"] = score_job_1_to_10(
            job.get("description", ""), job.get("title", ""), keywords, ceiling
        )
    return jobs
