# Job Tracker — Greater Munich, ESG / Sustainability roles

A personal job scraper that checks a curated list of company career
pages directly — no LinkedIn, StepStone, Indeed, or other aggregators.

This is a single-city, single-focus version of a tracker originally
built for a PM job search: it's scoped to **Greater Munich only**
(Munich itself plus commuter towns like Ottobrunn, Freising, Garching,
Dachau, and similar — see `src/matcher.py`, `MUNICH_KEYWORDS`), and
retitled to catch **ESG / Sustainability / Carbon Accounting / Climate**
roles instead of Project Manager roles.

- `config/companies.yaml` (174 companies) — rebuilt from scratch
  around ESG/Sustainability/Carbon/Climate relevance, not reused from
  the original PM/aerospace/defense-focused tracker. Three groups:
  climate-tech and ESG-software startups (Tanso, IntegrityNext,
  envoria, Ororatech, and others from Munich's climate-tech scene),
  ESG/sustainability consultancies (Anthesis, FTI Consulting, Baker
  Tilly, WTS Group, EurA, Ramboll, Drees & Sommer, ERM, ACCONSIS, the
  Big Four, TÜV SÜD), and large Munich-area corporates/institutions
  with genuine sustainability or ESG-reporting functions (finance/
  insurance, energy/utilities, industrials with real decarbonization
  programs, plus public sector and research). Everything defense/
  space/military-adjacent from the original list was dropped as not
  relevant to this search.
- `config/cv_profile.yaml` — derived from your CV: title keywords
  (ESG, Sustainability, Nachhaltigkeit, Carbon, Climate, CSRD, etc.)
  and scoring keywords (GHG Protocol, EXIOBASE/EORA, ISAE 3000, Scope
  1/2/3, EU Taxonomy, and your technical toolkit).

Feeds a single `data/job_tracker.xlsx` — one "Jobs" sheet, sorted by
relevance score (highest first). No score threshold is applied — every
job matching the title + Munich-area filter is shown, nothing is
excluded for scoring low.

**If you have zero GitHub experience, start with `SETUP_GUIDE.md`
instead of this file** — it walks through every click.

## How it works

1. Runs only when you trigger it — Actions tab → the workflow → "Run
   workflow" (or automatically every morning, ~06:30 Munich time; see
   `.github/workflows/daily_scrape.yml` if you want to change or turn
   off the schedule).
2. `src/main.py` scrapes every company in `config/companies.yaml`
   (via `src/ats_scrapers.py` — same underlying scraping engine as
   the original tracker, including a last-resort real-browser render
   for JavaScript-heavy career sites):
   - keeps only postings whose title looks like an ESG/sustainability
     role, AND whose location text explicitly confirms Munich or one
     of its commuter towns (not the whole surrounding state/country)
   - for postings that matched but came back with no description text,
     fetches the job's own posting page and pulls the text from there
   - scores everything that matched against your CV
     (`config/cv_profile.yaml`, via `src/matcher.py`) and adds it to
     the tracker (`src/tracker.py`)
3. The workflow commits the updated Excel file (and the generated
   `docs/index.html` webpage) back to the repository.
4. The monthly reset workflow (manual-trigger-only by default, can be
   put back on autopilot) archives the current tracker into
   `archive/` and starts fresh.

## Files you might want to edit

- `config/companies.yaml` — add/remove companies, or fix a
  `careers_url`/`board_token` if one isn't returning results.
- `config/cv_profile.yaml` — update if your CV changes, adjust which
  job titles count as a match, turn a score floor back on via
  `main_min_score` (0 = off by default), or tune `score_ceiling` if
  relevance scores cluster too high/low.

## Tracker columns

Job Posted (when the platform provides it — blank for companies where
it isn't available), Company, Job Title, Relevance Score (1-10, 10 =
best match to your CV), Location, and URL. Sorted by Relevance Score,
highest first, every run.

**Location filtering** is strict: a job only survives if its own
location text explicitly names Munich or one of its commuter towns
(see `src/matcher.py`, `MUNICH_KEYWORDS`) — not the whole surrounding
Bavaria/Germany. This matters because some platforms (Lever,
Greenhouse, SmartRecruiters, Workday) return a company's entire global
job board in one call, so a Munich-area company can easily have
postings in, say, Berlin or Frankfurt mixed in — those get dropped,
along with anything whose location is too vague to confirm at all
(blank, or just "Germany (Remote)").

## Running it yourself, locally (optional)

You don't need to do this — the whole point is that GitHub runs it
for you. But if you want to test a change before it goes live:

```bash
pip install -r requirements.txt
python -m src.main
```

## Known limitation, honestly stated

Company career pages are not standardized. This project handles the
common cases well (Greenhouse, Lever, Personio, SmartRecruiters, and
most Workday sites have clean data feeds behind them), falls back to
a best-effort HTML scrape for everything else, and as a last resort
retries with a real headless browser when that HTML scrape looks
suspiciously thin (see `SETUP_GUIDE.md` Part 9). That still won't
catch everything — see `SETUP_GUIDE.md` → "Adding or fixing a
company" for how to fix a specific one when you notice it's not
working.
