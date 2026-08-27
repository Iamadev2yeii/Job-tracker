# Job Tracker — Greater Munich, ESG / Sustainability roles

A personal job scraper that checks a curated list of company career
pages directly, plus a couple of genuinely legitimate exceptions:
Germany's official Bundesagentur für Arbeit API (Section 1 and 2),
and dedicated remote/EU job boards (Section 3 only). Deliberately
excludes LinkedIn, XING, StepStone, Indeed, and similar platforms:
all of them explicitly prohibit automated scraping in their terms of
service and actively enforce it technically (anti-bot detection,
CAPTCHAs, account bans) — see the note in
`config/arbeitsagentur_searches.yaml` for why the Bundesagentur API
is a different, legitimate case.

This is a single-city, single-focus version of a tracker originally
built for a PM job search: it's scoped to **Greater Munich only**
(Munich itself plus commuter towns like Ottobrunn, Freising, Garching,
Dachau, and similar — see `src/matcher.py`, `MUNICH_KEYWORDS`), and
retitled to catch **ESG / Sustainability / Carbon Accounting / Climate**
roles instead of Project Manager roles.

Feeds a single `data/job_tracker.xlsx` with **three sheets**:

- **"Jobs"** — sustainability/ESG-relevant roles in Munich, picked
  directly from company career pages only (no job boards or staffing
  agencies) plus the Bundesagentur API's real-employer search
  results. Sorted by relevance score (highest first).
- **"Munich Internships & Trainee"** — Praktikum/Trainee
  postings in Munich, in **any field**, not just sustainability and
  not restricted to office/admin work. Werkstudent (working-student)
  roles are deliberately excluded — internships and traineeships
  only. Still scored against Prabha's actual CV themes purely to
  rank results, never to exclude anything (no relevance floor). This
  sheet used to be called "General Roles" and covered generic
  permanent office/admin work — if your tracker file still has that
  old sheet, it's now an inert leftover; delete it by hand in Excel
  if you want it gone.
- **"Remote Sustainability (Europe)"** — fully remote ESG/
  sustainability roles anywhere in Europe (not scoped to Munich),
  sourced from dedicated climate/ESG job boards and EURES, the EU's
  own official job mobility portal. Senior/leadership titles are
  excluded everywhere, and this sheet specifically gives a small
  ranking boost to junior/entry-level titles.

No score threshold is applied on any sheet by default — every job
matching its title + location filter is shown, nothing is excluded
for scoring low.

**If you have zero GitHub experience, start with `SETUP_GUIDE.md`
instead of this file** — it walks through every click.

## How it works

1. Runs only when you trigger it — Actions tab → the workflow → "Run
   workflow" (or automatically every morning, ~06:30 Munich time; see
   `.github/workflows/daily_scrape.yml` if you want to change or turn
   off the schedule).
2. `src/main.py` scrapes two separate pools:
   - **Munich pool** (`config/companies.yaml` + `general_roles_companies.yaml`
     + `bavaria_directory_companies.yaml` + `arbeitsagentur_searches.yaml`)
     — each company is scraped once, then checked against BOTH the
     sustainability profile (→ "Jobs") and the internships/traineeships
     profile (→ "Munich Internships & Trainee"). A posting can
     legitimately land in both sheets if it matches both title lists.
   - **Remote pool** (`config/remote_sustainability_companies.yaml`)
     — scraped separately, checked against the remote-Europe profile,
     confirmed explicitly remote (not just blank-location) and not
     restricted to a non-EU country.
3. The workflow commits the updated Excel file (and the generated
   `docs/index.html` webpage) back to the repository.
4. The monthly reset workflow (manual-trigger-only by default, can be
   put back on autopilot) archives all three sheets into `archive/`
   and starts fresh.

## Files you might want to edit

- `config/companies.yaml` (187 companies) — add/remove companies, or
  fix a `careers_url`/`ats` that isn't returning results. Set
  `assume_local: true` on a company only if it's a genuine single-
  Munich-office company — see the comment at the top of the file for
  what that flag does. Feeds the "Jobs" sheet.
- `config/cv_profile.yaml` — the sustainability/ESG title list and
  scoring, feeds the "Jobs" sheet.
- `config/general_roles_companies.yaml` — general staffing agencies
  (Robert Half, DIS AG, Amadeus Fire, Randstad, Adecco, Manpower),
  international schools, and a few smaller Munich creative agencies.
  Feeds the "Munich Internships & Trainee" sheet alongside
  everything else in the Munich pool.
- `config/general_roles_profile.yaml` — internship/trainee-only title
  list (deliberately excludes Werkstudent and permanent roles), plus
  CV-relevance scoring keywords used purely for ranking. No score
  floor by design. Feeds the "Munich Internships & Trainee"
  sheet.
- `config/bavaria_directory_companies.yaml` — 165 Munich-metro-area
  companies imported from a broader Bavaria-wide business directory
  (mostly small local businesses, not sustainability-specific). Feeds
  both Munich sheets like everything else in the pool.
- `config/arbeitsagentur_searches.yaml` — real, targeted searches
  against Germany's official Bundesagentur für Arbeit Jobsuche API
  (a genuine public REST API, not a scraped website — see
  `src/ats_scrapers.py`, `scrape_arbeitsagentur`). Six sustainability
  searches (feed "Jobs") plus one broad `angebotsart=34`
  (Praktikum/Trainee, any field) search covering every employer
  within 30km of Munich (feeds "Munich Internships & Trainee"). Unlike
  agency sources, this API returns the real hiring employer for every
  posting.
- `config/esg_job_boards.yaml` — kept in the repo but **no longer
  loaded** into the Munich pool (NachhaltigeJobs.de, ClimateTechList,
  Randstad/Michael Page/Robert Walters/Hays/LP impact). Add it back in
  `src/main.py` if you ever want board/agency sources back in "Jobs."
- `config/remote_sustainability_companies.yaml` — dedicated remote/EU
  climate job boards (EuroClimateJobs.com, Climatebase, climate.jobs)
  plus EURES, the EU's own official job mobility portal. Feeds the
  "Remote Sustainability (Europe)" sheet — scraped separately from
  the Munich pool.
- `config/remote_sustainability_profile.yaml` — same field as
  `cv_profile.yaml`, but for the remote-Europe sheet. No score floor.

## How results are prioritized

- **Seniority**: postings with clear senior/leadership titles
  (Senior, Head of, Director, VP, Chief, Teamleiter, etc.) are
  excluded everywhere — the search targets associate/mid/junior/
  internship roles. Plain "Manager" titles are NOT excluded, since in
  German/EU postings that's a standard mid-level title, not "senior
  manager."
- **Internships**: the "Munich Internships & Trainee" sheet only
  accepts Praktikum/Praktikant/Trainee/Internship/Intern titles —
  Werkstudent is deliberately excluded, and the field doesn't matter
  (sustainability, marketing, finance, engineering, anything).
- **Recency**: postings that look 21 days old or newer (when the
  platform exposes a date at all) get a small score boost, so they
  sort nearer the top. Undated postings are neither boosted nor
  penalized — most platforms simply don't expose a date.

## Why matches are sparse, and what to expect

ESG/Sustainability is a narrow specialty — most companies have 0-2
such roles open on any given day, even at genuinely relevant
employers. The Bundesagentur searches should meaningfully raise the
hit rate for "Jobs" without relying on job boards, since they query
the largest job database in Germany directly. It still accumulates
daily and doesn't reset until the monthly archive, so the value
compounds over weeks rather than showing up all at once.

## Tracker columns

Job Posted (when the platform provides it — blank for companies where
it isn't available), Company, Job Title, Relevance Score (1-10, 10 =
best match), Location, and URL. Sorted by Relevance Score, highest
first, every run.

**Location filtering** on the two Munich sheets is strict: a job only
survives if its own location text explicitly names Munich or one of
its commuter towns (see `src/matcher.py`, `MUNICH_KEYWORDS`) — not the
whole surrounding Bavaria/Germany. This matters because some
platforms (Lever, Greenhouse, SmartRecruiters, Workday) return a
company's entire global job board in one call, so a Munich-area
company can easily have postings in, say, Berlin or Frankfurt mixed
in — those get dropped, along with anything whose location is too
vague to confirm at all (blank, or just "Germany (Remote)").

**Location filtering** on the remote-Europe sheet is different:
postings must explicitly say "remote" (not just have a blank
location) and must not explicitly restrict to a non-EU country — see
`src/matcher.py`, `REMOTE_KEYWORDS` and `NON_EU_ONLY_KEYWORDS`.

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
