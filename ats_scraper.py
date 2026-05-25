"""
ATS Career Page Scraper Router.
Handles scraping job listings from various Applicant Tracking Systems (ATS).
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from typing import List

import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("ats-scraper")
router = APIRouter(prefix="/api")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, application/xml;q=0.9, */*;q=0.8",
}


class ATSScrapeRequest(BaseModel):
    url: str


class JobListing(BaseModel):
    job_title: str
    company: str
    location: str
    job_type: str
    workplace_type: str
    posted: str
    source_url: str


class ATSScrapeResponse(BaseModel):
    jobs: List[JobListing]
    ats_detected: str


def clean_url(u: str) -> str:
    if not u:
        return ""
    u = str(u).strip()
    m = re.match(r"^\[(.*?)\]\((.*?)\)$", u)
    if m:
        return m.group(2)
    return u


def detect_ats(url: str) -> str:
    u = url.lower()
    if "greenhouse.io" in u:
        return "greenhouse"
    if "ashbyhq.com" in u:
        return "ashby"
    if "smartrecruiters.com" in u:
        return "smartrecruiters"
    if "workable.com" in u:
        return "workable"
    if "lever.co" in u:
        return "lever"
    if "personio.com" in u or "personio.de" in u:
        return "personio"
    if "salesforce-sites.com" in u:
        return "salesforce"
    if "charliehr.com" in u:
        return "charliehr"
    if "ats.rippling.com" in u:
        return "rippling"
    if "teamtailor" in u or "jenstencareers.co.uk" in u:
        return "teamtailor"
    if "jobs.apple.com" in u:
        return "apple"
    if "wd3.myworkdayjobs.com" in u or ".myworkdayjobs.com" in u:
        return "workday"
    if "avature.net" in u:
        return "avature"
    if "jobtrain.co.uk" in u:
        return "jobtrain"
    return "generic"


def first_path_token(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0] if parts else (urlparse(url).hostname or "").split(".")[0]


def hostname_subdomain(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.split(".")[0]


def safe_text(el):
    return el.get_text(" ", strip=True) if el else ""


def normalize_company_name(name: str) -> str:
    return str(name or "").replace("-", " ").strip().title()


def humanize_employment_type(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""

    split_value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    split_value = re.sub(r"[_\-]+", " ", split_value)
    split_value = re.sub(r"\s+", " ", split_value).strip().lower()

    mapping = {
        "fulltime": "Full-time",
        "full time": "Full-time",
        "parttime": "Part-time",
        "part time": "Part-time",
        "permanent": "Permanent",
        "fixedterm": "Fixed Term",
        "fixed term": "Fixed Term",
        "contract": "Contract",
        "contractor": "Contract",
        "temporary": "Temporary",
        "temp": "Temporary",
        "internship": "Internship",
        "intern": "Internship",
    }
    return mapping.get(split_value, value.strip())


def humanize_workplace_type(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""

    split_value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    split_value = re.sub(r"[_\-]+", " ", split_value)
    split_value = re.sub(r"\s+", " ", split_value).strip().lower()

    mapping = {
        "onsite": "On-Site",
        "on site": "On-Site",
        "on-site": "On-Site",
        "remote": "Remote",
        "hybrid": "Hybrid",
    }
    return mapping.get(split_value, value.strip())


def _make_job(job_title="", company="", location="", job_type="", workplace_type="", posted="", source_url=""):
    return {
        "job_title": str(job_title or "").strip(),
        "company": str(company or "").strip(),
        "location": str(location or "").strip(),
        "job_type": humanize_employment_type(job_type),
        "workplace_type": humanize_workplace_type(workplace_type),
        "posted": str(posted or "").strip(),
        "source_url": clean_url(source_url),
    }


def _dedupe_jobs(jobs: list) -> list:
    seen = set()
    unique_jobs = []
    for j in jobs:
        key = j.get("source_url") or f"{j.get('job_title','')}|{j.get('company','')}|{j.get('location','')}"
        if key not in seen:
            seen.add(key)
            unique_jobs.append(j)
    return unique_jobs


# ---------------- ATS Scrapers ----------------

def scrape_greenhouse(url: str):
    token = first_path_token(url)
    api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    r = requests.get(api, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    jobs = []

    for job in data.get("jobs", []):
        loc_obj = job.get("location") or {}
        location = loc_obj.get("name", "") if isinstance(loc_obj, dict) else str(loc_obj or "")

        job_type = ""
        workplace_type = ""

        for meta in job.get("metadata") or []:
            if not isinstance(meta, dict):
                continue

            meta_name = str(meta.get("name", "")).strip().lower()
            meta_value = str(meta.get("value", "")).strip()

            if meta_name in {"employment type", "job type", "contract type", "schedule"}:
                candidate = humanize_employment_type(meta_value)
                if candidate.lower() not in {"on-site", "onsite", "hybrid", "remote"}:
                    job_type = candidate
            elif meta_name in {"location type", "workplace type", "work type"}:
                workplace_type = humanize_workplace_type(meta_value)

        jobs.append(_make_job(
            job_title=job.get("title", ""),
            company=job.get("company_name") or normalize_company_name(token),
            location=location,
            job_type=job_type,
            workplace_type=workplace_type,
            posted=job.get("updated_at", ""),
            source_url=job.get("absolute_url", ""),
        ))
    return jobs


def scrape_ashby(url: str):
    token = first_path_token(url)
    api = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
    r = requests.get(api, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    jobs = []

    for job in data.get("jobs", []):
        location = ""
        if isinstance(job.get("location"), dict):
            location = job["location"].get("locationName", "") or job["location"].get("name", "")
        elif isinstance(job.get("location"), str):
            location = job["location"]

        jobs.append(_make_job(
            job_title=job.get("title", ""),
            company=normalize_company_name(token),
            location=location,
            job_type=job.get("employmentType", ""),
            workplace_type=job.get("workplaceType", ""),
            posted=job.get("publishedAt", job.get("publishedDate", job.get("createdAt", ""))),
            source_url=job.get("jobUrl", ""),
        ))
    return jobs


def scrape_smartrecruiters(url: str):
    company = first_path_token(url)
    api = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
    try:
        r = requests.get(api, headers={"Accept": "application/json", **HEADERS}, timeout=30)
        r.raise_for_status()
        data = r.json()
        postings = data.get("content", [])
        jobs = []

        for job in postings:
            loc = job.get("location", {})
            workplace_type = ""

            if isinstance(loc, dict):
                location = loc.get("fullLocation") or ", ".join(
                    x for x in [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")] if x
                )
                if loc.get("remote"):
                    workplace_type = "Remote"
                elif loc.get("hybrid"):
                    workplace_type = "Hybrid"
            else:
                location = str(loc or "")

            type_obj = job.get("typeOfEmployment", "") or job.get("employmentType", "")
            if isinstance(type_obj, dict):
                job_type = type_obj.get("id") or type_obj.get("label", "")
            else:
                job_type = str(type_obj or "")

            source_url = clean_url(job.get("ref", "") or job.get("url", ""))

            jobs.append(_make_job(
                job_title=job.get("name", ""),
                company=job.get("company", {}).get("name", normalize_company_name(company)) if isinstance(job.get("company"), dict) else normalize_company_name(company),
                location=location,
                job_type=job_type,
                workplace_type=workplace_type,
                posted=job.get("releasedDate", ""),
                source_url=source_url,
            ))
        return jobs
    except Exception:
        return []


def scrape_workable(url: str):
    token = first_path_token(url)
    endpoints = [
        f"https://apply.workable.com/api/v1/widget/accounts/{token}",
        f"https://apply.workable.com/api/v1/widget/accounts/{token}/jobs",
    ]

    for api in endpoints:
        try:
            r = requests.get(api, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            jobs_raw = data.get("jobs", data.get("results", []))
            jobs = []

            for job in jobs_raw:
                city = str(job.get("city", "") or "").strip()
                state = str(job.get("state", "") or "").strip()
                country = str(job.get("country", "") or "").strip()
                location = ", ".join(x for x in [city, state, country] if x)
                workplace_type = "Remote" if job.get("telecommuting") else ""

                source_url = clean_url(job.get("url", "") or job.get("shortlink", ""))
                if not source_url and job.get("shortcode"):
                    source_url = f"https://apply.workable.com/j/{job.get('shortcode')}"

                jobs.append(_make_job(
                    job_title=job.get("title", ""),
                    company=normalize_company_name(token),
                    location=location,
                    job_type=job.get("employment_type", ""),
                    workplace_type=workplace_type,
                    posted=job.get("published_on", job.get("published", job.get("created_at", ""))),
                    source_url=source_url,
                ))
            return jobs
        except Exception:
            continue
    return []


def scrape_lever(url: str):
    token = first_path_token(url) or hostname_subdomain(url)
    api = f"https://api.lever.co/v0/postings/{token}?mode=json"
    r = requests.get(api, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    jobs = []

    for job in data:
        cats = job.get("categories", {}) or {}
        location = str(cats.get("location", "") or "").strip()
        workplace_type = "Remote" if "remote" in location.lower() else ""

        jobs.append(_make_job(
            job_title=job.get("text", ""),
            company=normalize_company_name(token),
            location=location,
            job_type=cats.get("commitment", ""),
            workplace_type=workplace_type,
            posted=job.get("createdAt", ""),
            source_url=job.get("hostedUrl", ""),
        ))
    return jobs


def scrape_personio(url: str):
    host = urlparse(url).hostname or ""
    api = f"https://{host}/xml?language=en"
    try:
        r = requests.get(api, headers=HEADERS, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        jobs = []

        for job in root.findall(".//position"):
            jobs.append(_make_job(
                job_title=job.findtext("name", default=""),
                company=host.split(".")[0].title(),
                location=job.findtext("office", default=""),
                job_type=job.findtext("employmentType", default=""),
                workplace_type="",
                posted=job.findtext("createdAt", default=""),
                source_url=job.findtext("url", default=""),
            ))
        return jobs
    except Exception:
        return []


def scrape_rippling(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []

    data = json.loads(script.string)
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    items = []

    for q in queries:
        payload = q.get("state", {}).get("data", {})
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            items = payload.get("items", [])
            break

    jobs = []
    for job in items:
        locs = job.get("locations", []) or []
        location = ", ".join(x.get("name", "") for x in locs if x.get("name"))
        workplace_types = sorted({
            humanize_workplace_type(x.get("workplaceType", ""))
            for x in locs if x.get("workplaceType")
        })
        workplace_type = ", ".join(x for x in workplace_types if x)

        jobs.append(_make_job(
            job_title=job.get("name", job.get("title", "")),
            company=normalize_company_name(first_path_token(url)),
            location=location,
            job_type="",
            workplace_type=workplace_type,
            posted="",
            source_url=job.get("url", ""),
        ))
    return jobs


def scrape_generic(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []

    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(k in href for k in ["/job/", "/jobs/", "/careers/", "/vacancy/"]):
            title = safe_text(a)
            if title and len(title) > 5:
                jobs.append(_make_job(
                    job_title=title,
                    company="Unknown",
                    location="",
                    job_type="",
                    workplace_type="",
                    posted="",
                    source_url=urljoin(url, a["href"]),
                ))

    return _dedupe_jobs(jobs)


@router.post("/scrape-ats", response_model=ATSScrapeResponse)
async def scrape_ats(request: ATSScrapeRequest):
    """Scrape job listings from a career page URL."""
    url = request.url
    ats = detect_ats(url)
    logger.info(f"Scraping jobs from {url} (Detected ATS: {ats})")

    jobs_raw = []
    try:
        if ats == "greenhouse":
            jobs_raw = scrape_greenhouse(url)
        elif ats == "ashby":
            jobs_raw = scrape_ashby(url)
        elif ats == "smartrecruiters":
            jobs_raw = scrape_smartrecruiters(url)
        elif ats == "workable":
            jobs_raw = scrape_workable(url)
        elif ats == "lever":
            jobs_raw = scrape_lever(url)
        elif ats == "personio":
            jobs_raw = scrape_personio(url)
        elif ats == "rippling":
            jobs_raw = scrape_rippling(url)
        else:
            jobs_raw = scrape_generic(url)

        jobs = []
        for j in jobs_raw:
            jobs.append(JobListing(
                job_title=j.get("job_title", "Unknown Title"),
                company=j.get("company", "Unknown Company"),
                location=j.get("location", ""),
                job_type=j.get("job_type", ""),
                workplace_type=j.get("workplace_type", ""),
                posted=j.get("posted", ""),
                source_url=j.get("source_url", "")
            ))

        return ATSScrapeResponse(jobs=jobs, ats_detected=ats)

    except Exception as e:
        logger.error(f"Scrape failed for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")