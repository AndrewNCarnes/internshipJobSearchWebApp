# Internship Monitor — Project Handoff

Context dump for picking up work on Andrew Carnes' internship-monitoring web app.

## UPDATE 2026-09-19 (read first — supersedes §6, §7 and §11 below)

- **45 scrapers registered** in `master_runner.SCRAPERS`. Added this session:
  amentum, caterpillar, eli_lilly, ge_aerospace, ge_vernova, sierra_space,
  textron, kratos, general_dynamics, anduril_industries, relativity_space,
  ursa_major, stoke_space, redwire, firefly_aerospace, beehive_industries,
  terran_orbital, disney, henkel, knorr_bremse, mitsubishi_heavy_industries,
  enfra, iron_will_ventures.
- **New shared/reference code:** `probe_board.py` (was missing — rebuilt),
  `dry_run.py` (test a scraper live without writing the DB),
  `directemployers_common.py` (Textron, ENFRA). New platform references:
  Pereless (kratos), ClearCompany (firefly), Rippling (beehive), BambooHR
  (iron_will), Azure-search/SuccessFactors (knorr_bremse), GD CareerSearch.
- **Filter changes:** `classify_location()` / `is_remote_only()` added;
  Workday bare-city sites ("Evendale") now resolved via the detail endpoint's
  country code; "national capital region" removed from US cities (was
  admitting Metro Manila); more foreign countries added.
- **Bugs fixed:** `add_company.py` crashed registering (cp1252 vs emoji in
  master_runner.py); `eli_lilly.py` and `l3harris.py` returned partial dicts on
  failure (silent-wipe risk); tests referenced the removed Lockheed scraper.
- **Not scrapeable:** Quanta (iCIMS CAPTCHA wall — do not bypass), Belcan (no
  public NA board since Cognizant; resume form only), Parametric Solutions
  (posts only to ZipRecruiter/Indeed).
- **Open:** "MAG Engineering" and "Aircraft Systems" need Andrew to name the
  exact company; NASA Pathways needs a USAJOBS API key from Andrew;
  `scrapers/new_york_air_break.py.unverified` is obsolete (knorr_bremse covers
  NYAB) and can be deleted; `gate_input.json` in the root is a leftover test file.

Everything below reflects the state as of the end of the PREVIOUS session.

---

## 1. What this project is

An automated internship finder for a mechanical engineering undergrad (UCF, B.S.
ME expected May 2028) looking for **Summer 2027 engineering internships in the
USA**. It scrapes company job boards nightly, stores results in SQLite, and
displays them on a Streamlit dashboard.

- **Repo:** `https://github.com/AndrewNCarnes/internshipJobSearchWebApp` (public)
- **Local path:** `D:\BOTS\Job Search Macro`
- **Deployed:** `https://internships.streamlit.app`
- **Local Python:** `C:\Python314` (Python 3.14 — see Known Issues, this causes crashes)

### The local/deployed split (important)

The app runs in two places with **different capabilities**:

| | Local (`localhost:8501`) | Deployed (Streamlit Cloud) |
|---|---|---|
| View job data | yes | yes |
| "Run Scrapers Now" button | yes | **hidden** |
| Add-a-Company panel | yes | **hidden** |
| Read-only caption | no | yes |

This is controlled by `runtime_mode.is_local()`, which checks for a **marker file
`.local_dev`** in the project root. That file is in `.gitignore`, so it exists on
Andrew's PC but never in the copy Streamlit Cloud clones from GitHub. That
asymmetry is the entire mechanism.

**Why controls are hidden when deployed:** Streamlit Cloud containers are
ephemeral (results wiped on restart), have no Playwright browser binaries (so
browser-based scrapers fail outright), and would scrape from Streamlit's IP.

### Data flow

```
master_runner.py (local, runs 24/7 in a terminal)
  -> runs each scraper as a subprocess
  -> scrapers write to master_jobs.db via database.save_jobs()
  -> push_results() commits master_jobs.db + status.txt and pushes to GitHub
  -> Streamlit Cloud auto-redeploys, public dashboard shows new data
```

`master_jobs.db` is **committed to the repo on purpose** — the deployed app has
no other data source. Do NOT add it to `.gitignore`.

---

## 2. File tree

```
Job Search Macro/
├── .gitignore                   # .local_dev, __pycache__/, *.pyc
├── .local_dev                   # marker file, NOT committed
├── requirements.txt             # streamlit, pandas, requests, beautifulsoup4
│                                #   (Playwright deliberately omitted — can't work on Cloud)
├── dashboard.py                 # Streamlit UI
├── master_runner.py             # nightly loop + SCRAPERS list
├── database.py                  # SQLite layer
├── location_filter.py           # shared US/internship filters (CRITICAL — see §4)
├── runtime_mode.py              # is_local() / mode_label()
├── git_push.py                  # push_results()
├── workday_common.py            # shared Workday scraper logic (the "Workday template")
├── add_company.py               # self-service scraper generator (probe/generate/smoke-test)
├── dashboard_add_company.py     # Streamlit panel driving add_company.py via subprocess
├── probe_board.py               # NEW: generic platform/endpoint probe (see §7)
├── test_harness.py / run_tests.py / verify_all.py
├── master_jobs.db               # committed — deployed app reads this
├── status.txt                   # heartbeat timestamp, committed
├── templates/
│   ├── greenhouse.py.tmpl
│   ├── lever.py.tmpl
│   └── ashby.py.tmpl
└── scrapers/
    ├── (see §6 for full list + status)
    ├── probe.py, probe_quanta.py, probe_kraft.py, probe_lilly.py
    └── new_york_air_break.py.unverified   # quarantined
```

---

## 3. The scraper contract

**Every scraper must follow this exact shape.** Deviating breaks the runner or,
worse, silently deletes data.

```python
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Company Name"     # used by find_existing_scraper() matching

def get_current_jobs():
    """Return {job_id: {"title","location","url"}} on success, or None on failure."""
    ...

def run_monitor():
    print(f"Starting {COMPANY_NAME} USA Internship Check...")
    current_jobs = get_current_jobs()
    if current_jobs is None:
        print(f"[{COMPANY_NAME}] scrape failed; skipping save to protect stored rows.")
        return
    new_count, deleted_count = database.save_jobs(COMPANY_NAME, current_jobs)
    ...

if __name__ == "__main__":
    run_monitor()
```

### THE MOST IMPORTANT RULE: None vs {}

`database.save_jobs(company, jobs)` **deletes every stored row for that company
that is not in the dict you pass.** Therefore:

- **Return `None`** when the scrape *failed* (timeout, HTTP error, endpoint
  changed, unparseable response). `run_monitor` then skips the save entirely and
  stored rows survive.
- **Return `{}`** only when the board genuinely has zero matching internships
  (e.g. ULA is summer-only and empty off-season). This correctly prunes stale rows.

Confusing these two causes a **silent wipe**: a transport error looks like "this
company has no internships anymore" and every row disappears. This has been
caught multiple times; treat it as the primary invariant.

Corollary: an empty result from a *changed endpoint* is a failure, not an empty
board. See the Kraft Heinz story in §5.

---

## 4. location_filter.py — shared filters (never bypass)

Two functions everything depends on:

- `is_us_location(location_name, company="")` — is this a US location?
- `is_internship_title(title)` — is this actually an internship/co-op?
- `is_plausible_internship(title)` — looser check used when a Workday facet has
  already tagged something as Intern/Co-op (facets are evidence, not proof —
  J&J files Postdoctoral Scholars under Intern/Co-op).

### Never reinstate a per-scraper copy of this logic

Older scrapers carried their own `is_us_location` with a substring whitelist.
Real damage from that, confirmed against live data:

| Location | Old private filter | Why |
|---|---|---|
| `Milwaukee, Wisconsin` | **dropped** | `"uk"` matched Milwa-**uk**-ee |
| `Indianapolis, Indiana` | **dropped** | `"india"` matched **India**-napolis |
| `Madison, Wisconsin` | **dropped** | WI not in the 11-state whitelist |
| `Davenport, Iowa` | **dropped** | IA not in the whitelist either |

And the old `\bintern` title regex had no closing boundary, so
`International Brand Manager` and `Internal Audit Analyst` were **kept** —
about 37% of the database was junk like that at one point. `is_internship_title`
uses `\b(intern|interns|internship|internships|coop|coops|co op|co ops)\b` and
normalizes separators (`_`, `/`, `-`) to spaces first, so `Program_Internship`
still matches.

### KNOWN TRAP: the `CA` collision

`is_us_location` matches bare two-letter codes against US state codes, and
**`CA` is California**. Any board that emits `countryCode = "CA"` for **Canada**
produces `"Mississauga, ON, CA"`, which trips the US signal, skips the
foreign-country veto, and imports Canadian jobs as US. **Confirmed live on SPX.**

Current workaround is per-scraper: spell the country out before filtering
(`"Mississauga, ON, Canada"`) so the country-name veto fires. See
`AMBIGUOUS_COUNTRY` in `scrapers/viable_engineering.py` and
`COUNTRY_CODE_NAMES` in `scrapers/spx.py`. Same class of ambiguity: `IN`
(India/Indiana), `DE` (Germany/Delaware).

**Open improvement:** fix this once centrally in `location_filter.py` — check
`INTERNATIONAL_CODES` (ON/QC/BC…) *before* letting a trailing `CA` count as a US
signal. That would protect every scraper instead of patching each one.

### Other location notes

- `"Multiple Locations"` is in `REMOTE_SIGNALS` and returns **True**. On a
  Sweden-heavy board (Saab: 409 SE vs 69 US postings) that would import Swedish
  jobs as US. `saab.py` has `SKIP_MULTIPLE_LOCATIONS = True` for this reason.
- Ambiguous cities resolve correctly already: `Bristol, Rhode Island` → US,
  bare `Bristol` → UK. The filter checks US states before foreign cities. Don't
  "fix" this.
- Unrecognized locations print `location not recognized, skipping: ...` and are
  dropped. Because the title filter runs first, this only fires for
  internship-titled rows, so the log stays quiet.

---

## 5. Platform playbook

Each ATS has a working reference implementation. When adding a company, identify
the platform first (use `probe_board.py`), then copy the matching reference.

| Platform | Approach | Reference scraper |
|---|---|---|
| **Workday** | POST `/wday/cxs/{tenant}/{site}/jobs` | `workday_common.py` + thin per-company file |
| **Phenom People** | POST `/widgets` with `ddoKey=refineSearch` | `scrapers/roush_engineering.py`, `scrapers/eli_lilly.py` |
| **SuccessFactors (RMK)** | `sitemal.xml` RSS feed (misspelling is real), or `/search-jobs?startrow=N` | `scrapers/ula.py` (feed), `scrapers/spx.py` (HTML) |
| **ADP WorkforceNow** | GET `/careercenter/public/events/staffing/v1/job-requisitions?cid=&ccId=` | `scrapers/viable_engineering.py` |
| **Eightfold** | GET `/api/pcsx/search?domain=&query=&start=` | `scrapers/kraft_heinz.py` |
| **Custom server-rendered** | plain `requests` + BeautifulSoup | `scrapers/saab.py` |
| **Greenhouse / Lever / Ashby** | JSON API | use `add_company.py` — auto-templated |
| **iCIMS** | detected but NOT templatable yet | — open thread |

### Workday specifics (`workday_common.py`)

Usage: `run_workday_monitor(COMPANY_NAME, TENANT, SITE, WD)` — per-company files
are ~12 lines.

Two hard-won behaviors baked in:

1. **Facets lie.** Workday exposes a `workerSubType`/`jobType` facet for
   "Intern (Fixed Term)", but employers tag inconsistently. Blue Origin's facet
   reported 1 intern while a text search found 17 — trusting it would have
   deleted 16 real postings. So the facet is only trusted when it reports at
   least `MIN_FACET_TRUST = 5` hits; otherwise it falls back to `searchText="intern"`.
2. **Multi-site postings collapse.** A posting open at several sites reports
   `locationsText` as `"9 Locations"` — a string with no state in it, which no
   filter can pass. Those get a second-pass lookup against the job's own detail
   endpoint (`additionalLocations`).

Also: Workday returns 403 without `Origin` and `Referer` headers, and rejects
`limit > 20` on these tenants. Requests go through Playwright's request context
to get past Cloudflare.

**Tenant names are NOT guessable.** Amentum's tenant is `pae` (legacy PAE
tenant from the merger), not `amentum`. Always confirm.

### Phenom specifics

Needs a site-specific `refNum`, found in CDN asset paths:
`cdn.phenompeople.com/CareerConnectResources/{REFNUM}/` — note the variant
`CareerConnectResources/prod/{REFNUM}/`, which the regex must tolerate.
Known: Roush `ROUROUUS`, Lilly `LILLUS`.

### The Lilly lesson (important, generalizable)

Eli Lilly has a **real Workday job URL** (`lilly.wd5.myworkdayjobs.com/en-US/LLY/job/...`)
but every CXS site path returned **HTTP 422** across 7 candidate site names and 5
payload shapes. Its actual public board is **Phenom** (`careers.lilly.com/us/en`).

**A direct Workday job link does not mean the board is scrapeable via Workday's
API.** Check the careers domain itself, not a job URL. Phenom-over-Workday is a
common stack.

### The Kraft Heinz lesson

Eightfold **migrated** from `/api/apply/v2/jobs` to `/api/pcsx/search`. The old
scraper listened for the retired endpoint, intercepted nothing, and would have
reported zero jobs. The `None` guard is what prevented a wipe. The rewrite:

- Loads the page once to establish cookies/session, then calls the PCSX endpoint
  **directly** through Playwright's request context, paging on `start` — this
  gets the full result set instead of only what lazy-rendered.
- Uses a **recursive `_find_jobs()`** that walks the JSON for the first list of
  posting-shaped dicts, because the key under `data` is undocumented and has
  already changed once.

---

## 6. Scraper inventory

### Registered in `master_runner.SCRAPERS` (22)

blue_origin, boeing, castelion, ford, hermeus, honda, honeywell,
johnson_and_johnson, kraft_heinz, l3harris, northrop_grumman, otto_aerospace,
raytheon, rocketlab, roush_engineering, saab, siemens, spacex, spx, ula,
vast_space, viable_engineering

**`lockheed_martin.py` exists but is deliberately NOT registered** — Andrew
removed it on purpose. Note: because `save_jobs` only prunes a company when its
scraper runs, any existing Lockheed rows will persist indefinitely.

### Built last session, not yet added to SCRAPERS (5)

| File | Platform | Status |
|---|---|---|
| `scrapers/amentum.py` | Workday `pae` / `Amentum_Careers` / wd1 | ran clean |
| `scrapers/caterpillar.py` | Workday `cat` / `CaterpillarCareers` / wd5 | ran clean |
| `scrapers/sierra_space.py` | Workday `sierraspace` / `Sierra_Space_External_Career_Site` / wd1 | ran clean |
| `scrapers/ge_aerospace.py` | Workday `geaerospace` / `GE_ExternalSite` / wd5 | written, UNTESTED |
| `scrapers/eli_lilly.py` | Phenom `LILLUS` | rewritten from Workday, UNTESTED |

Sierra Space note: two tenants are live — `sierraspace` (current) and `snc`
(legacy Sierra Nevada Corp), same site path. If it starts 403ing, try `snc`
before assuming breakage.

### Earlier in the session (5, all working)

`ula.py`, `viable_engineering.py`, `spx.py`, `saab.py`, `roush_engineering.py`

Note on Viable Engineering: it returns 0 internships, and that is **correct** —
the board has 4 openings, all non-intern (PLC Programmer, Materials Engineer,
Mechatronics Engineer, Robotics Software Engineer). Verified via probe.

---

## 7. Tooling

### `probe_board.py` (generic, new — use this first)

```
python probe_board.py https://careers.example.com/jobs/
python probe_board.py https://example.com/careers --show-all
```

Reports: detected platform (fingerprints 18 ATSs), whether the page is
server-rendered or client-rendered, and every XHR that looks like job data with
its JSON keys and list counts. Verified against all six platforms encountered
this session.

**Guessing platforms from company names has been wrong repeatedly.** Always probe.

### `add_company.py` + `dashboard_add_company.py`

Self-service scraper generation from the dashboard. Pipeline: probe → generate →
**live** smoke test → verdict. Auto-templates Greenhouse, Lever, and Ashby.
Verdicts: `verified` (added + live), `empty`, `error`, `no_template`, `detected`,
`undetected`, `exists`. Anything not `verified` is written as a `.unverified`
file, excluded from the nightly run.

Dashboard drives it via **subprocess** because Playwright's sync API refuses to
start off the main thread, which is where Streamlit runs everything.

**Patches applied last session (all verified present in the repo):**

1. `register_in_master()` wrote `"roush.py"` where every other entry is
   `"scrapers/roush.py"` — so every auto-added company was registered but
   **never actually ran** (subprocess exits 2, logged as an error, batch
   continues). Now matches the existing path style and is idempotent.
2. Added `find_existing_scraper()` — checks the slug filename, then scans every
   scraper's `COMPANY_NAME`, so typing "Roush" finds `roush_engineering.py`
   despite the different filename. Uses **word-token subset** matching
   (`{roush} ⊆ {roush, engineering}`), not string prefix — a prefix test wrongly
   blocked "Saabre" against "Saab".
3. Added `--force` flag + `exists` verdict + a Force checkbox in the panel.
   The guard runs before any network call, so duplicates cost zero requests.

**Prior bugs fixed (earlier session, for context):** UnicodeEncodeError from
cp1252 consoles; missing Ashby template; orphaned staging `.pyc` files;
URL-vs-token ordering (the token `quanta` matched an unrelated SF startup's
Ashby board); generic-word token collision (`New York Air Brake` → token `new`
→ a stranger's Greenhouse board). The last one is why there's now a stopword
list, acronym generation, and a board-belongs-to-company content check.

### Other probes

`probe_kraft.py`, `probe_lilly.py`, `probe_quanta.py`, `probe.py` — one-off
diagnostics. **Not scrapers**; they have no `run_monitor()` and must never be
added to `SCRAPERS`.

---

## 8. Dashboard notes

`dashboard.py` scores every title (`internship_score`) to sort false positives to
the bottom — `STRONG` / `FALSE_FRIEND` / `SENIORITY` / `SEASON` / `ADVANCED` /
`GRADUATE` / `RELEVANT` regexes produce a 0–100ish score and a tier
(`core` / `maybe` / `unlikely`). The sidebar has a "Hide likely false positives"
toggle defaulting to on.

`format_location()` collapses 40-site Workday postings to the first 4 with a
`+N more` hover tooltip, otherwise one card grows taller than the whole grid.

**`st.set_page_config()` must be the first Streamlit command.** The
Add-a-Company panel used to be called above it; it's since been moved below and
gated behind `is_local()`.

---

## 9. Known issues / open threads

### Python 3.14 crashes the local dashboard

```
Fatal Python error: _PySemaphore_Wakeup: parking_lot: ReleaseSemaphore failed (error: 6)
```

Interpreter-level bug (no user code in the traceback). Happens during Streamlit's
file-watcher teardown when a browser session disconnects. **Fix:** create
`.streamlit/config.toml` with:

```toml
[server]
fileWatcherType = "none"
```

Better long-term fix: run the project on Python 3.12/3.13, which is what
Streamlit, watchdog, and Playwright are actually tested against. Deployed app is
unaffected (Cloud runs its own Python).

### Scrapers still carrying private filters

`honda.py` is the **last** scraper with its own `is_us_location` and the old
`\bintern` regex — same Milwaukee/Indianapolis bugs described in §4. Needs the
same treatment `kraft_heinz.py` received.

### Scrapers without a `None` failure guard

A crude grep suggested `castelion`, `ford`, `l3harris`, `lockheed_martin`,
`siemens`, `spacex` may lack one. Worth a real read rather than trusting the grep.
Note `blue_origin.py` returns a **partial dict** on exception, which can look
like "these are all the jobs" and prune the rest — `workday_common.py` fixed this
for its users but `blue_origin.py` itself still has the old behavior.

### Repo hygiene

`__pycache__/` is committed. `.gitignore` now lists it, but ignoring doesn't
untrack — run `git rm -r --cached __pycache__` once.

### Templates

- **Workday template: DONE** — that's `workday_common.py`.
- **iCIMS template: open.** `probe_quanta.py` is written and ready to run
  against Quanta's board; its output would tell us whether iCIMS is templatable
  (JSON feed or clean iframe) or manual-tier (bot wall).
- Phenom is now effectively templatable too (two constants), but hasn't been
  turned into a `.tmpl` yet.

### Filename typo

`new_york_air_break.py.unverified` — should be "brake". If it's ever promoted,
the module name will be wrong. Also note: the duplicate guard now treats this
file as an existing scraper, so re-running "New York Air Brake" says
"already has a scraper" — use `--force` or delete the file first.

---

## 10. Features requested but not built

1. **Discord notification** when the daily run completes. Design agreed: a
   `discord_notify.py` posting to a webhook, reading new rows from the DB
   directly (master_runner runs scrapers as subprocesses so it never sees
   `save_jobs` return values). Webhook URL must be a secret — env var
   `DISCORD_WEBHOOK_URL` or a gitignored `.discord_webhook` file. Must respect
   Discord's 2000-char cap. Never raises (a Discord outage must not stop scraping).
2. **Search bar** in the dashboard — filter `view` on title/location/company,
   `regex=False` so a query like `C++` or `(Paid)` can't crash it. Insert after
   the `hide_unlikely` filter.
3. **Password gate** — single shared password from `st.secrets`, gated on
   `st.session_state`, `hmac.compare_digest` for the check. Andrew's stated goal
   was "so people can't use it for free" — worth being clear this is NOT a
   payment system: one shared password, no per-user accounts, no revocation. Real
   monetization needs per-user auth + a payment processor.
4. **Push-on-demand** (tagged for later): `push_results()` currently runs only in
   the scheduled loop, so clicking "Run Scrapers Now" scrapes but does **not**
   publish. To enable, call `push_results()` in the button handler after
   `run_all_scrapers()`. Andrew explicitly wants to revisit this.

---

## 11. Remaining companies to scrape (24)

### Probe requested — commands already given, output pending (4)

| Company | Known so far |
|---|---|
| Textron (Bell Flight & Textron Aviation) | NLX/DirectEmployers front-end over **Taleo**; job list renders client-side ("Loading...") |
| GE Vernova | unknown |
| Kratos Defense | likely iCIMS |
| General Dynamics | likely iCIMS; varies by subsidiary |

```
python probe_board.py https://careers.textron.com/jobs/
python probe_board.py https://www.gevernova.com/careers
python probe_board.py https://www.kratosdefense.com/careers
python probe_board.py https://www.gd.com/careers
```

### Likely one-click via `add_company.py` (7)

Relativity Space, Stoke Space, Ursa Major, Firefly Aerospace, Anduril
Industries, Beehive Industries, Redwire Space

Use the URL field if auto-detect struggles — a bare company name can match a
stranger's board (see the NYAB bug).

### Terran Orbital (1)

Greenhouse token **confirmed**: `terranorbitalcorporation` (31 open jobs).
Company name is "Terran Orbital", not "Terra Norbital" as originally written.
Currently **0 internships**, so `add_company.py` will smoke-test clean and
quarantine it as `empty` — correct behavior; promote the `.unverified` manually.
Now a Lockheed Martin subsidiary but keeps its own board.

### Large, platform unknown (5)

Walt Disney Imagineering, Mitsubishi, Henkel, Belcan, New York Air Brake
(Knorr-Bremse)

### Special cases (2)

- **Quanta** — iCIMS confirmed. `probe_quanta.py` is ready to run. Unblocks the
  iCIMS template.
- **NASA (Pathways and OSTEM)** — highest-value target for this user. USAJOBS has
  a documented public API (free key) covering Pathways; OSTEM is a separate
  system. Genuinely different from everything else here; worth doing alone.

### Small companies — may have no scrapeable board (5)

Parametric Solutions, Enfra Solutions, Iron Will Ventures, MAG Engineering,
Aircraft Systems

Small firms often post only to Indeed, use ADP, or hand-build a page. Running
`probe_board.py` on these will likely resolve several to "no board, skip it,"
which shortens the list for free.

### Already done / duplicates

Honda was already built before the list was given. Amentum appeared twice.

---

## 12. Working principles (learned the hard way)

1. **Never trust a mock.** Every single bug this project has hit was found by
   hitting a live board, not by testing against fixtures. Mock data can't produce
   a coincidental token collision, a cp1252 console, a migrated endpoint, or a
   `CA` country code. The live smoke-test gate in `add_company.py` caught all of
   them.
2. **Never guess a platform from a company name.** Probe it.
3. **`None` on failure, `{}` on genuine emptiness.** The single most important
   invariant in the codebase.
4. **Distinguish "board read fine, no interns" from "something broke."** Every
   scraper prints `scanned N listings; M are US internships` for exactly this.
   Off-season zeros are normal (ULA is summer-only; SPX and Saab had no US
   interns in recent postings).
5. **Log what you skip.** Silent drops are how bad filters hide. Ambiguous
   locations, unresolvable multi-site postings, and missing locations all print.
6. **Keep filters central.** Every per-scraper copy of location logic has
   eventually diverged and lost real jobs.
