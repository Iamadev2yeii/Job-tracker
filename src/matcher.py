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
    # Country/region-level terms — added after a US-located internship
    # slipped through on a company flagged assume_local: true. A
    # blank location on that kind of company defaults to a Munich
    # match, but a location that DOES say "USA" (just not one of the
    # specific cities listed above) was falling through as merely
    # "unconfirmed" rather than a clear mismatch, so assume_local
    # wrongly overrode it. These catch that regardless of which
    # specific city is named.
    "usa", "u.s.a", "united states", "us-based", "canada", "mexico",
    "brazil", "united kingdom", "australia", "new zealand", "china",
    "south korea", "india", "japan", "united arab emirates", "dubai",
]


def title_matches(title: str, must_match: list[str]) -> bool:
    t = title.lower()
    return any(term.lower() in t for term in must_match)


def _combined_text(job: dict[str, Any]) -> str:
    """
    Title plus description, when a description is available. Several
    scrapers (Greenhouse, Lever, Personio — see src/ats_scrapers.py)
    already capture full description text as part of their normal
    listing call, at zero extra network cost — that text was sitting
    unused before this, since matching only ever looked at the title.
    A permanent role with a generic German title ("Referent (m/w/d)")
    but ESG-specific duties described in the body is a real, common
    pattern this now catches for free. Companies scraped generically
    (no description available) are unaffected — this just adds a
    second field to check when there's something in it.
    """
    return f"{job.get('title', '')} {job.get('description', '')}"


# Titles clearly pitched at senior/leadership level — excluded per
# request, since the search is meant to surface associate/mid/junior
# roles instead. Deliberately narrow: "manager" is NOT on this list,
# since in German/EU postings "Manager" is a standard mid-level title
# (e.g. "Sustainability Manager"), not equivalent to an English
# "senior manager." Only unambiguous seniority markers are excluded.
SENIORITY_EXCLUDE = [
    "senior", "sr.", "principal", "head of", "director", "vp ",
    "vice president", "chief sustainability", "chief esg", "geschäftsführer",
    "abteilungsleiter", "bereichsleiter", "teamleiter", "leitung",
]


def is_excluded_seniority(title: str) -> bool:
    t = title.lower()
    return any(term in t for term in SENIORITY_EXCLUDE)


# Titles that explicitly name a language requirement Prabha doesn't
# have (her CV lists German B2/C1 and, implicitly, English — nothing
# else). Added after a Korean-language-required internship scored
# highly and appeared in "Munich Internships & Trainee": the broad,
# any-field Bundesagentur search (angebotsart=34, no keyword) will
# happily surface market-specific trainee programs (Korea/China/Japan
# desks are common at Munich corporates) that Prabha has no realistic
# way to do. This is deliberately checked against the TITLE only —
# a language mentioned only deep in a job description is often a
# "nice to have," but one prominent enough to be in the title is
# almost always a hard requirement.
LANGUAGE_EXCLUDE = [
    "korean", "koreanisch", "chinese", "chinesisch", "mandarin",
    "japanese", "japanisch", "russian", "russisch", "arabic", "arabisch",
    "turkish", "türkisch", "vietnamese", "vietnamesisch", "thai",
    "hindi", "polish", "polnisch",
]


def is_excluded_language(title: str) -> bool:
    t = title.lower()
    return any(term in t for term in LANGUAGE_EXCLUDE)


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
    Relevance check (title + description when available — see
    _combined_text) plus seniority and language exclusion (title
    only — see is_excluded_seniority/is_excluded_language's own
    docstrings for why those stay title-scoped). No location decision
    yet (see main.py's scrape_munich docstring for why that's done
    later, after enrichment).
    """
    must_match = cv_profile.get("title_must_match", [])
    return [
        job for job in jobs
        if job.get("title")
        and title_matches(_combined_text(job), must_match)
        and not is_excluded_seniority(job["title"])
        and not is_excluded_language(job["title"])
    ]


def filter_internships(
    jobs: list[dict[str, Any]], profile: dict[str, Any], sustainability_related_company: bool = False
) -> list[dict[str, Any]]:
    """
    For the "Munich Internships & Trainee" sheet specifically: a job
    must be BOTH an internship/trainee-type posting (title_must_match,
    checked against the TITLE ONLY — whether something IS an
    internship is a title-level classification, not something a
    permanent role's description can turn it into) AND relevant to
    Prabha's actual field (relevance_keywords, checked against title
    + description when available — see _combined_text) — UNLESS the
    posting comes from a company whose core business already IS that
    field (sustainability_related_company=True, set per-entry in
    companies.yaml), in which case any internship/trainee title at
    that company counts, even a generically titled one like
    "Praktikum Marketing" at a climate-tech startup.

    This is deliberately narrower than an earlier version of this
    sheet, which accepted ANY-field internships — that surfaced things
    like a Korean-market trainee program with no connection to
    Prabha's background at all. Seniority and language exclusion still
    apply, checked against the title only.
    """
    must_match = profile.get("title_must_match", [])
    relevance_keywords = profile.get("relevance_keywords", [])
    result = []
    for job in jobs:
        title = job.get("title", "")
        if not title:
            continue
        if not title_matches(title, must_match):
            continue
        if is_excluded_seniority(title) or is_excluded_language(title):
            continue
        if sustainability_related_company or not relevance_keywords or title_matches(_combined_text(job), relevance_keywords):
            result.append(job)
    return result


def resolve_munich_match(job: dict[str, Any], search_text: str | None = None, assume_local: bool = False) -> bool:
    """
    Decides whether a single already title-matched job is confirmed
    in the Munich metro area. Searches search_text if given (e.g. an
    enriched full-page text blob), otherwise falls back to
    job["location"]. Only "confirmed" counts as a match — "mismatch"
    is always dropped.

    assume_local: for companies with a single Munich office (mostly
    small local startups — see the `assume_local: true` flag in
    companies.yaml), a blank/"unconfirmed" location almost always just
    means the platform didn't expose a location field, not that the
    job is elsewhere. For those companies only, "unconfirmed" is
    treated as a pass too, instead of being dropped like it is for
    every other (typically multi-city/multi-country) company.
    """
    text = search_text if search_text is not None else job.get("location", "")
    status = location_status(text)
    if status == "confirmed":
        return True
    if status == "unconfirmed" and assume_local:
        return True
    return False


# --- Berlin matching, for the "Jobs - Berlin" sheet — same idea as
# Munich matching (a specific metro area, not remote), just a
# different city and its own commuter belt (mostly Brandenburg towns
# rather than Bavarian ones).
BERLIN_KEYWORDS = [
    "berlin", "potsdam", "falkensee", "bernau", "oranienburg",
    "königs wusterhausen", "koenigs wusterhausen", "werder", "teltow",
    "kleinmachnow", "strausberg", "eichwalde", "schönefeld", "schoenefeld",
    "hennigsdorf", "velten", "birkenwerder", "ludwigsfelde", "rangsdorf",
    "zeuthen", "wildau", "erkner", "fürstenwalde", "fuerstenwalde",
]


def berlin_location_status(location: str) -> str:
    """Same three-state logic as location_status, but for Berlin
    metro area confirmation instead of Munich."""
    if not location:
        return "unconfirmed"
    loc = location.lower()
    if any(kw in loc for kw in BERLIN_KEYWORDS):
        return "confirmed"
    # Reuses OTHER_MAJOR_LOCATIONS as the "named a different city"
    # check, plus Munich itself and its commuter towns — a job
    # explicitly in Munich should obviously not count as Berlin.
    if any(kw in loc for kw in OTHER_MAJOR_LOCATIONS) or any(kw in loc for kw in MUNICH_KEYWORDS):
        return "mismatch"
    return "unconfirmed"


def resolve_berlin_match(job: dict[str, Any], search_text: str | None = None, assume_local: bool = False) -> bool:
    """
    Berlin-area equivalent of resolve_munich_match — same
    assume_local reasoning (a blank location at a genuine single-
    Berlin-office company is treated as a pass instead of dropped).
    """
    text = search_text if search_text is not None else job.get("location", "")
    status = berlin_location_status(text)
    if status == "confirmed":
        return True
    if status == "unconfirmed" and assume_local:
        return True
    return False


# --- Remote / Europe matching, for the "Remote Sustainability
# (Europe)" sheet — a different question than Munich matching: not
# "is this in one specific metro area," but "does this posting
# explicitly say it's remote, and is there nothing ruling out Europe."

REMOTE_KEYWORDS = [
    "remote", "100% remote", "fully remote", "remote-first", "remote first",
    "remote-only", "work from anywhere", "distributed team", "telecommute",
    "home-based", "vollständig remote", "100% homeoffice",
]

# Phrases that restrict a "remote" role to somewhere outside Europe —
# these override a REMOTE_KEYWORDS match, since "remote" alone doesn't
# mean "remote and eligible from Europe."
NON_EU_ONLY_KEYWORDS = [
    "us only", "usa only", "u.s. only", "united states only", "us-based only",
    "us based only", "must be based in the us", "must be based in the united states",
    "eligible to work in the us", "eligible to work in the united states",
    "us citizens only", "canada only", "must be based in canada",
    "apac only", "australia only", "latam only", "latin america only",
    "india only", "must reside in the us", "us residents only",
]


def remote_eu_status(text: str) -> str:
    """
    Returns "confirmed" (explicitly remote, nothing rules out Europe),
    "mismatch" (explicitly restricted to a non-EU country), or
    "unconfirmed" (no remote language found at all, or too vague to
    tell).
    """
    if not text:
        return "unconfirmed"
    t = text.lower()
    if any(kw in t for kw in NON_EU_ONLY_KEYWORDS):
        return "mismatch"
    if any(kw in t for kw in REMOTE_KEYWORDS):
        return "confirmed"
    return "unconfirmed"


def resolve_remote_eu_match(job: dict[str, Any], search_text: str | None = None) -> bool:
    """
    Decides whether a title-matched job is confirmed remote-and-
    Europe-eligible. Deliberately strict compared to resolve_munich_match
    — there's no assume_remote equivalent, since the boards this
    feeds (config/remote_sustainability_companies.yaml) mix remote,
    hybrid, on-site, and non-European postings, so a blank location
    can't be safely assumed to mean "remote in Europe." Only postings
    that explicitly say "remote" (and don't explicitly rule out
    Europe) pass.
    """
    text = search_text if search_text is not None else job.get("location", "")
    return remote_eu_status(text) == "confirmed"


# Titles signaling entry/early-career level — given a small scoring
# boost in the remote-Europe pipeline specifically (see
# _junior_boost), per request to prioritize junior over mid-level
# without excluding mid-level entirely.
JUNIOR_KEYWORDS = [
    "junior", "graduate", "entry level", "entry-level", "associate",
    "trainee", "early career", "intern", "internship", "praktikum",
    "praktikant",
]


def _junior_boost(title: str) -> int:
    t = title.lower()
    return 2 if any(kw in t for kw in JUNIOR_KEYWORDS) else 0


def extract_location_snippet(text: str, window: int = 30) -> str:
    """
    For a Munich-area job whose location was recovered from a full
    page-text blob (see main.py's enrichment step) rather than a clean
    structured field, pulls a short window of text around wherever a
    Munich keyword was actually found — so the tracker's Location
    column shows something readable instead of the entire fetched
    page. Falls back to "Munich" if no keyword position is found
    (shouldn't happen if this text really matched, but defensive
    regardless).
    """
    return _extract_keyword_snippet(text, MUNICH_KEYWORDS, window, fallback="Munich")


def extract_remote_snippet(text: str, window: int = 30) -> str:
    """
    Same idea as extract_location_snippet, but for the remote-Europe
    pipeline — pulls a window around whichever REMOTE_KEYWORDS phrase
    matched, with a "Remote" fallback instead of "Munich". Using
    extract_location_snippet here was a real bug: its Munich-specific
    fallback meant every remote-Europe posting with a blank location
    that needed a snippet ended up literally labeled "Munich" in the
    tracker, which is exactly backwards for a sheet whose entire point
    is that Munich is NOT where these jobs are.
    """
    return _extract_keyword_snippet(text, REMOTE_KEYWORDS, window, fallback="Remote")


def extract_berlin_snippet(text: str, window: int = 30) -> str:
    """Same idea as extract_location_snippet, but for the Berlin
    pipeline — pulls a window around a BERLIN_KEYWORDS match, with a
    "Berlin" fallback instead of "Munich"."""
    return _extract_keyword_snippet(text, BERLIN_KEYWORDS, window, fallback="Berlin")


def _extract_keyword_snippet(text: str, keywords: list[str], window: int, fallback: str) -> str:
    loc = text.lower()
    for kw in keywords:
        idx = loc.find(kw)
        if idx != -1:
            start = max(0, idx - window)
            end = min(len(text), idx + len(kw) + window)
            snippet = text[start:end].strip()
            return ("..." if start > 0 else "") + snippet + ("..." if end < len(text) else "")
    return fallback


import datetime as _dt
import re as _re


def _parse_posted_date(posted_date: str, today: "_dt.date | None" = None) -> "_dt.date | None":
    """
    Best-effort parse of whatever posted_date text a platform exposes
    (formats vary a lot — ISO dates, DD.MM.YYYY, or relative text like
    "3 days ago" / "vor 3 Tagen"). Returns None if it can't be read,
    which is common since many platforms don't expose a date at all.
    """
    if not posted_date:
        return None
    today = today or _dt.date.today()
    s = posted_date.strip().lower()
    if "today" in s or "heute" in s:
        return today
    if "yesterday" in s or "gestern" in s:
        return today - _dt.timedelta(days=1)
    m = _re.search(r"(\d+)\s*(day|tag)", s)
    if m:
        return today - _dt.timedelta(days=int(m.group(1)))
    m = _re.search(r"(\d+)\s*(hour|stunde)", s)
    if m:
        return today
    m = _re.search(r"(\d+)\s*(week|woche)", s)
    if m:
        return today - _dt.timedelta(weeks=int(m.group(1)))
    m = _re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        try:
            return _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _recency_boost(posted_date: str, today: "_dt.date | None" = None) -> int:
    """+3 to the final score if the posting looks 21 days old or
    newer; 0 otherwise (including when the date can't be read at
    all — undated postings are neither boosted nor penalized)."""
    d = _parse_posted_date(posted_date, today)
    if d is None:
        return 0
    today = today or _dt.date.today()
    days_old = (today - d).days
    if 0 <= days_old <= 21:
        return 3
    return 0


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


def score_jobs(jobs: list[dict[str, Any]], cv_profile: dict[str, Any], junior_priority: bool = False) -> list[dict[str, Any]]:
    """Adds relevance_score to every job in place (including a small
    boost for postings that look recent — see _recency_boost, and
    optionally another for junior/entry-level titles — see
    _junior_boost). Does not filter anything out — score is for
    ranking/sorting only."""
    keywords = cv_profile.get("scoring_keywords", [])
    ceiling = cv_profile.get("score_ceiling", DEFAULT_SCORE_CEILING)
    today = _dt.date.today()
    for job in jobs:
        base = score_job_1_to_10(job.get("description", ""), job.get("title", ""), keywords, ceiling)
        boost = _recency_boost(job.get("posted_date", ""), today)
        if junior_priority:
            boost += _junior_boost(job.get("title", ""))
        job["relevance_score"] = max(1, min(10, base + boost))
    return jobs
