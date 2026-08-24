# Atlanta Corporate Job Tracker

Checks Chick-fil-A, Coca-Cola, Inspire Brands, Newell Brands, and CNN
(Warner Bros. Discovery) every 8 hours for corporate job postings in
Atlanta, Duluth, or Alpharetta, GA — scores each one against your resume,
and pushes a phone notification for anything worth a look.

## How it works

- Runs on **GitHub Actions**, on a schedule (`0 */8 * * *` — every 8 hours),
  so it works even if your laptop is off.
- Pulls live postings from each company's actual applicant-tracking system:
  - Coca-Cola, Inspire Brands, CNN/WBD → Workday's public job-search API (reliable, structured data)
  - Chick-fil-A → iCIMS (HTML scrape — see note below)
  - Newell Brands → their career site (HTML scrape — see note below)
- Keeps a `seen_jobs.json` file so you're only notified about a posting **once**.
- Scores each posting Low / Medium / High against your resume (strategy,
  brand, AI strategy, consulting, product/growth signals — see "How matching
  works" below) and skips anything that reads as an hourly/restaurant/intern role.
- Sends a push notification via [ntfy.sh](https://ntfy.sh) — a free,
  no-signup notification service. You just install the ntfy app and
  subscribe to your topic name.

## 5-minute setup

1. **Create a GitHub repo** and upload these files (`scraper.py`,
   `requirements.txt`, `seen_jobs.json`, `requirements.txt`,
   `.github/workflows/job-check.yml`) to it — either via the GitHub web
   uploader, or:
   ```bash
   git init
   git add .
   git commit -m "Initial job tracker"
   git branch -M main
   git remote add origin https://github.com/<you>/atlanta-job-tracker.git
   git push -u origin main
   ```

2. **Pick a private ntfy topic name.** This acts like a password — anyone
   who knows the exact topic name can see your notifications, since ntfy.sh
   topics aren't authenticated by default. Pick something long and random,
   e.g. `jack-prommel-atl-jobs-x7f2q9`.

3. **Install the ntfy app** on your phone ([iOS](https://apps.apple.com/us/app/ntfy/id1625396347) /
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)),
   open it, and subscribe to that exact topic name.

4. **Add the topic as a GitHub secret** so it's never committed to the repo in
   plain text:
   - Repo → Settings → Secrets and variables → Actions → New repository secret
   - Name: `NTFY_TOPIC`
   - Value: the topic name you picked in step 2

5. **Enable Actions** on the repo (Actions tab → "I understand my workflows,
   go ahead and enable them"). The workflow already has `contents: write`
   permission set so it can save its own progress (`seen_jobs.json`) back to
   the repo after each run — no extra setup needed there.

6. **Trigger a test run**: Actions tab → "Atlanta Job Check" → "Run workflow".
   Check the logs, and check your phone for notifications.

From then on, it runs automatically every 8 hours.

## How matching works

`scraper.py` scores each job title (and location text) against keyword
groups pulled from your resume — strategy, corporate strategy, brand
management, brand strategy, AI strategy / generative AI, growth,
GTM/go-to-market, product management, consulting, business analyst — with
extra weight for manager/senior/lead titles, and a penalty for
intern/hourly/crew/warehouse titles (so restaurant-level Chick-fil-A
listings, which dominate their postings, get filtered out rather than
spamming you).

- **High** — strong overlap with your background (e.g. "Corporate Strategy
  Manager", "Brand Strategy Lead")
- **Medium** — plausible stretch/adjacent role (e.g. "Senior Business
  Analyst", "Marketing Manager")
- **Low** — still corporate, but a weaker fit — shown so you don't miss
  something the keyword list didn't anticipate
- **Skip** — reads as hourly/restaurant/intern-level, not notified

This is a blunt keyword heuristic, not a real evaluation of fit — treat the
label as a triage signal, not gospel. Skim the "Low" tier occasionally in
case something good slipped through with unusual phrasing.

## Known limitations / maintenance notes

- **Chick-fil-A (iCIMS) and Newell Brands scrapers are HTML-based**, since
  neither exposes a clean public JSON API the way Workday does. Career sites
  restyle periodically, which can silently break these two adapters. If you
  stop getting Chick-fil-A/Newell notifications for a while, that's the
  likely cause — open the company's job search page, view page source, and
  adjust the CSS selectors in `fetch_icims_jobs()` / `fetch_generic_html_jobs()`
  in `scraper.py` accordingly.
- The Workday adapters (Coca-Cola, Inspire Brands, CNN/WBD) are on a much
  more stable, well-documented API pattern and shouldn't need maintenance.
- CNN postings are filtered out of Warner Bros. Discovery's combined Workday
  site by requiring "CNN" to appear in the title or location — if WBD tags
  postings differently, adjust the `title_filter` logic.
- If a run fails, check the Actions tab logs — the workflow logs a warning
  per company/city rather than crashing the whole run, so one broken adapter
  won't block notifications from the others.

## Adjusting things later

- **Change target cities**: edit `TARGET_CITIES` at the top of `scraper.py`.
- **Add a company**: add a new adapter call in `main()` — reuse
  `fetch_workday_jobs()` if the company runs on Workday (check their
  `careers.<company>.com` link — if it redirects to `*.myworkdayjobs.com`,
  you're set).
- **Tune matching**: edit `STRONG_KEYWORDS` / `SENIORITY_GOOD` /
  `SENIORITY_BAD` in `scraper.py`.
- **Change frequency**: edit the cron line in
  `.github/workflows/job-check.yml` (still capped at 8h to be considerate to
  these sites, but you can space it out further, e.g. `0 */12 * * *` for
  every 12 hours).
