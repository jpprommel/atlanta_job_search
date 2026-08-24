#!/usr/bin/env python3
"""
Atlanta Corporate Job Tracker
------------------------------
Checks career pages at Chick-fil-A, Coca-Cola, Inspire Brands, Newell Brands,
and CNN (Warner Bros. Discovery) for new corporate job postings in
Atlanta / Duluth / Alpharetta, GA, scores each posting against Jack's resume,
and pushes new matches to a phone via ntfy.sh.

Designed to run on a schedule (every 8 hours via GitHub Actions cron).
State (which jobs have already been seen/notified) is kept in seen_jobs.json
so the same posting is never pushed twice.
"""

import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

TARGET_CITIES = ["Atlanta", "Duluth", "Alpharetta"]

# ntfy.sh topic to push to. Set this via the NTFY_TOPIC environment variable
# (GitHub Actions secret) rather than hardcoding it — anyone who knows your
# topic name can read your notifications, since ntfy topics are unauthenticated
# by default unless you self-host or use a reserved/protected topic.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "jack-prommel-atl-jobs-CHANGE-ME")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

STATE_FILE = Path(__file__).parent / "seen_jobs.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 20

# ----------------------------------------------------------------------------
# RESUME-BASED MATCH SCORING
# ----------------------------------------------------------------------------
# Keyword groups pulled from Jack's resume + stated interests. Weighted so a
# title needs several strong signals to hit "High".

STRONG_KEYWORDS = {
    "strategy": 3, "corporate strategy": 4, "brand": 2, "brand management": 4,
    "brand strategy": 4, "ai strategy": 4, "artificial intelligence": 3,
    "generative ai": 3, "growth": 2, "network growth": 3, "gtm": 3,
    "go-to-market": 3, "go to market": 3, "product manager": 3,
    "product management": 3, "consultant": 2, "consulting": 2,
    "business analyst": 2, "analytics": 2, "commercial strategy": 4,
    "insights": 1, "innovation": 1, "transformation": 2,
}

SENIORITY_GOOD = {"manager": 2, "senior": 2, "sr.": 2, "lead": 1, "director": 1,
                   "principal": 1, "associate manager": 1}
SENIORITY_BAD = {"intern": -6, "internship": -6, "crew": -8, "team member": -8,
                  "hourly": -6, "cashier": -8, "restaurant": -5,
                  "entry level": -3, "co-op": -5, "student": -4, "driver": -6,
                  "warehouse": -5, "technician": -3}


def score_job(title, extra_text=""):
    """Return (label, score, matched_terms) for a job title against the resume."""
    text = f"{title} {extra_text}".lower()
    score = 0
    matched = []

    for kw, weight in STRONG_KEYWORDS.items():
        if kw in text:
            score += weight
            matched.append(kw)

    for kw, weight in SENIORITY_GOOD.items():
        if kw in text:
            score += weight

    for kw, weight in SENIORITY_BAD.items():
        if kw in text:
            score += weight
            matched.append(f"(-) {kw}")

    if score >= 7:
        label = "High"
    elif score >= 3:
        label = "Medium"
    elif score > -3:
        label = "Low"
    else:
        label = "Skip"  # almost certainly an hourly/restaurant/intern role

    return label, score, matched


# ----------------------------------------------------------------------------
# ADAPTER: WORKDAY (Coca-Cola, Inspire Brands, CNN / Warner Bros. Discovery)
# ----------------------------------------------------------------------------

def fetch_workday_jobs(company, tenant, wd_host, site, title_filter=None):
    """
    Query a Workday CXS job-search API for each target city.
    title_filter: optional substring the job title/team must contain
                  (used for CNN, since it's one brand within WBD's Workday site).
    """
    base = f"https://{tenant}.{wd_host}.myworkdayjobs.com"
    endpoint = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    results = {}

    for city in TARGET_CITIES:
        body = {
            "appliedFacets": {},
            "limit": 20,
            "offset": 0,
            "searchText": city,
        }
        try:
            resp = requests.post(endpoint, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [warn] Workday fetch failed for {company} ({city}): {e}")
            continue

        for posting in data.get("jobPostings", []):
            title = posting.get("title", "").strip()
            loc = posting.get("locationsText", "") or ""
            ext_path = posting.get("externalPath", "")
            job_id = posting.get("bulletFields", [ext_path])[0] if posting.get("bulletFields") else ext_path

            if not any(c.lower() in loc.lower() for c in TARGET_CITIES):
                continue
            if title_filter and title_filter.lower() not in title.lower() and title_filter.lower() not in loc.lower():
                continue

            url = f"{base}/en-US/{site}{ext_path}"
            results[job_id or url] = {
                "company": company,
                "title": title,
                "location": loc,
                "url": url,
                "posted": posting.get("postedOn", ""),
            }

    return list(results.values())


# ----------------------------------------------------------------------------
# ADAPTER: iCIMS (Chick-fil-A Corporate)
# ----------------------------------------------------------------------------

def fetch_icims_jobs(company, subdomain):
    """
    Chick-fil-A's ATS (iCIMS) is server-rendered HTML rather than a clean JSON
    API. This scrapes the search results page per target city.
    NOTE: iCIMS occasionally changes its markup — if this stops finding
    results, re-check the selectors below against a live page view-source.
    """
    results = {}
    base = f"https://{subdomain}.icims.com"

    for city in TARGET_CITIES:
        params = {
            "searchKeyword": "",
            "searchLocation": f"{city}, GA",
            "mobile": "false",
            "in_iframe": "1",
        }
        try:
            resp = requests.get(f"{base}/jobs/search", params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [warn] iCIMS fetch failed for {company} ({city}): {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        # iCIMS classic layout: each job is a <tr> with class containing "row" inside
        # a table with id/class containing "iCIMS_JobsTable"; title is in an <a> with
        # class containing "title", location in a <span>/<div> with class containing
        # "location". Selectors below are intentionally loose to survive minor
        # markup drift; tighten them if you get false positives.
        rows = soup.select("tr[id*='job'], .iCIMS_JobsTable tr, .iCIMS_JobRow")
        if not rows:
            # Fallback: any link that looks like a job detail link
            rows = soup.select("a[href*='/jobs/']")

        for row in rows:
            link = row if row.name == "a" else row.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link["href"]
            if not title or "/jobs/" not in href:
                continue
            full_url = href if href.startswith("http") else base + href
            loc_el = row.find(class_=re.compile("location", re.I)) if row.name != "a" else None
            loc_text = loc_el.get_text(strip=True) if loc_el else city

            results[full_url] = {
                "company": company,
                "title": title,
                "location": loc_text,
                "url": full_url,
                "posted": "",
            }

    return list(results.values())


# ----------------------------------------------------------------------------
# ADAPTER: Generic HTML search (Newell Brands)
# ----------------------------------------------------------------------------

def fetch_generic_html_jobs(company, search_url_template, base_url):
    """
    Best-effort scraper for career sites that don't expose a documented JSON
    API. Fetches the search page per city and grabs every link whose href
    looks like a job-detail link, using the link text as the title.
    NOTE: this is the least precise adapter — verify results manually the
    first few runs and tighten the href pattern / add a CSS selector once
    you've viewed the page source, since generic career-site templates vary.
    """
    results = {}
    for city in TARGET_CITIES:
        url = search_url_template.format(city=urllib.parse.quote(city))
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [warn] HTML fetch failed for {company} ({city}): {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = soup.select("a[href*='/job/'], a[href*='job-detail'], a[href*='/jobs/']")

        for link in candidates:
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if not title or len(title) < 4 or not href:
                continue
            full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
            # Only keep it if the city name shows up somewhere near the link
            # (parent element text) — crude but filters out nav junk.
            parent_text = link.parent.get_text(" ", strip=True) if link.parent else ""
            if city.lower() not in parent_text.lower() and city.lower() not in title.lower():
                continue
            results[full_url] = {
                "company": company,
                "title": title,
                "location": city,
                "url": full_url,
                "posted": "",
            }

    return list(results.values())


# ----------------------------------------------------------------------------
# STATE / DEDUPE
# ----------------------------------------------------------------------------

def load_seen():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(seen, indent=2, sort_keys=True))


# ----------------------------------------------------------------------------
# NOTIFICATIONS
# ----------------------------------------------------------------------------

def send_ntfy(job, label, matched):
    title = f"{label} match: {job['company']}"
    body = (
        f"{job['title']}\n"
        f"{job['location']}\n"
        f"Signals: {', '.join(matched) if matched else 'n/a'}\n"
        f"{job['url']}"
    )
    priority = {"High": "high", "Medium": "default", "Low": "low"}.get(label, "min")
    try:
        requests.post(
            NTFY_URL,
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "briefcase",
                "Click": job["url"],
            },
            timeout=REQUEST_TIMEOUT,
        )
        print(f"  [notified] {job['company']}: {job['title']}")
    except Exception as e:
        print(f"  [warn] ntfy push failed: {e}")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    all_jobs = []

    print("Checking Coca-Cola (Workday)...")
    all_jobs += fetch_workday_jobs("Coca-Cola", "coke", "wd1", "coca-cola-careers")

    print("Checking Inspire Brands (Workday)...")
    all_jobs += fetch_workday_jobs("Inspire Brands", "inspirebrands", "wd5", "InspireCareers")

    print("Checking CNN / Warner Bros. Discovery (Workday)...")
    all_jobs += fetch_workday_jobs("CNN", "warnerbros", "wd5", "global", title_filter="CNN")

    print("Checking Chick-fil-A Corporate (iCIMS)...")
    all_jobs += fetch_icims_jobs("Chick-fil-A", "careers-chickfila")

    print("Checking Newell Brands...")
    all_jobs += fetch_generic_html_jobs(
        "Newell Brands",
        "https://jobs.newellbrands.com/search/?q={city}",
        "https://jobs.newellbrands.com",
    )

    seen = load_seen()
    new_count = 0

    for job in all_jobs:
        key = job["url"]
        if key in seen:
            continue

        label, score, matched = score_job(job["title"], job.get("location", ""))
        seen[key] = {
            "company": job["company"],
            "title": job["title"],
            "label": label,
            "first_seen": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

        if label == "Skip":
            print(f"  [skip] {job['company']}: {job['title']} (looks hourly/entry-level)")
            continue

        send_ntfy(job, label, matched)
        new_count += 1

    save_seen(seen)
    print(f"\nDone. {len(all_jobs)} postings checked, {new_count} new notifications sent.")


if __name__ == "__main__":
    sys.exit(main())
