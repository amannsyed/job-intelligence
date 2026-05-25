"""
ATS Career Page Scraper Router.
Handles scraping job listings from various Applicant Tracking Systems (ATS).
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
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
    "Accept-Language": "en-US,en;q=0.9",
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


def normalize_company_name(name: str) -> str:
    return str(name or "").replace("-", " ").replace("_", " ").strip().title()


def first_path_token(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0] if parts else (urlparse(url).hostname or "").split(".")[0]


def hostname_subdomain(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.split(".")[0]


def safe_text(el):
    return el.get_text(" ", strip=True) if el else ""


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
        "full-time": "Full-time",
        "parttime": "Part-time",
        "part time": "Part-time",
        "part-time": "Part-time",
        "permanent": "Permanent",
        "fixedterm": "Fixed Term",
        "fixed term": "Fixed Term",
        "fixed-term": "Fixed Term",
        "contract": "Contract",
        "contractor": "Contract",
        "temporary": "Temporary",
        "temp": "Temporary",
        "internship": "Internship",
        "intern": "Internship",
        "fulltimeemployee": "Full-time",
        "parttimeemployee": "Part-time",
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


def normalize_greenhouse_url(url: str) -> str:
    url = clean_url(url)
    m = re.search(r"/jobs/([0-9.]+e[+-]?[0-9]+)$", url, re.I)
    if not m:
        return url
    sci = m.group(1)
    try:
        fixed = str(int(float(sci)))
        return re.sub(r"/jobs/[0-9.]+e[+-]?[0-9]+$", f"/jobs/{fixed}", url, flags=re.I)
    except Exception:
        return url


def normalize_posted(value) -> str:
    if value is None:
        return ""
    dt = parse_posted_datetime(value)
    if dt == datetime.min.replace(tzinfo=timezone.utc):
        return ""
    return dt.strftime("%d/%m/%Y")


def parse_posted_datetime(value) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    s = str(value).strip()
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)

    if re.fullmatch(r"\d{13}", s):
        try:
            return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    if re.fullmatch(r"\d{10}", s):
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass

    return datetime.min.replace(tzinfo=timezone.utc)


def sort_jobs_by_posted(jobs: list) -> list:
    return sorted(jobs, key=lambda j: parse_posted_datetime(j.get("posted")), reverse=True)


def infer_workplace_type_from_location(location: str) -> str:
    s = str(location or "").lower()
    if not s:
        return ""
    if "hybrid" in s:
        return "Hybrid"
    if "remote" in s or "fully remote" in s or "work from home" in s:
        return "Remote"
    if "on-site" in s or "on site" in s or "onsite" in s:
        return "On-Site"
    return ""


def clean_location_text(location: str) -> str:
    if not location:
        return ""
    out = str(location).strip()
    out = re.sub(r"\s*[·|]\s*(Remote|Hybrid|On[- ]?Site)\s*$", "", out, flags=re.I)
    out = re.sub(r"\s*\((Remote|Hybrid|On[- ]?Site)\)\s*$", "", out, flags=re.I)
    out = re.sub(r"\s+", " ", out).strip(" ,;")
    return out


def _normalize_jsonld_value(value):
    if isinstance(value, list):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, dict):
        return value.get("name", "") or value.get("@value", "") or ""
    return str(value or "").strip()


def enrich_job_fields(job: dict) -> dict:
    job["job_title"] = str(job.get("job_title", "") or "").strip()
    job["company"] = str(job.get("company", "") or "").strip()
    job["location"] = str(job.get("location", "") or "").strip()
    job["job_type"] = humanize_employment_type(job.get("job_type", ""))
    job["workplace_type"] = humanize_workplace_type(job.get("workplace_type", ""))
    job["posted"] = normalize_posted(job.get("posted", ""))
    job["source_url"] = clean_url(job.get("source_url", ""))

    if job["location"] and not job["workplace_type"]:
        inferred = infer_workplace_type_from_location(job["location"])
        if inferred:
            job["workplace_type"] = inferred

    if job["location"]:
        job["location"] = clean_location_text(job["location"])

    if not job["job_type"] and job["job_title"]:
        title = job["job_title"].lower()
        if "intern" in title:
            job["job_type"] = "Internship"
        elif "contract" in title:
            job["job_type"] = "Contract"

    return job


def _make_job(job_title="", company="", location="", job_type="", workplace_type="", posted="", source_url=""):
    return enrich_job_fields({
        "job_title": str(job_title or "").strip(),
        "company": str(company or "").strip(),
        "location": str(location or "").strip(),
        "job_type": job_type,
        "workplace_type": workplace_type,
        "posted": posted,
        "source_url": source_url,
    })


def _dedupe_jobs(jobs: list) -> list:
    seen = set()
    unique_jobs = []
    for j in jobs:
        j = enrich_job_fields(j)
        key = j.get("source_url") or f"{j.get('job_title','')}|{j.get('company','')}|{j.get('location','')}"
        if key not in seen:
            seen.add(key)
            unique_jobs.append(j)
    return unique_jobs


def _extract_location_from_jsonld(job_item: dict) -> str:
    job_loc = job_item.get("jobLocation")
    if isinstance(job_loc, list) and job_loc:
        job_loc = job_loc[0]

    if isinstance(job_loc, dict):
        addr = job_loc.get("address", {}) or {}
        if isinstance(addr, dict):
            return ", ".join(
                x for x in [
                    addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""),
                    addr.get("addressCountry", "")
                ] if x
            )
    return ""


def _extract_jobs_from_jsonld(soup: BeautifulSoup, fallback_company: str = "") -> list:
    jobs = []

    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        stack = data if isinstance(data, list) else [data]

        while stack:
            item = stack.pop(0)

            if isinstance(item, list):
                stack.extend(item)
                continue

            if not isinstance(item, dict):
                continue

            if "@graph" in item and isinstance(item["@graph"], list):
                stack.extend(item["@graph"])

            item_type = item.get("@type")
            if item_type == "JobPosting":
                company = ""
                hiring_org = item.get("hiringOrganization")
                if isinstance(hiring_org, dict):
                    company = hiring_org.get("name", "") or fallback_company
                elif isinstance(hiring_org, str):
                    company = hiring_org

                workplace_type = ""
                if str(item.get("jobLocationType", "")).upper() == "TELECOMMUTE":
                    workplace_type = "Remote"

                jobs.append(_make_job(
                    job_title=_normalize_jsonld_value(item.get("title")),
                    company=company or fallback_company,
                    location=_extract_location_from_jsonld(item),
                    job_type=_normalize_jsonld_value(item.get("employmentType")),
                    workplace_type=workplace_type,
                    posted=_normalize_jsonld_value(item.get("datePosted")),
                    source_url=_normalize_jsonld_value(item.get("url")),
                ))

    return _dedupe_jobs(jobs)


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
    if "personio.com" in u or "personio.de" in u or ".jobs.personio.com" in u:
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
    if "avature.net" in u:
        return "avature"
    return "generic"


# ---------------- ATS Scrapers ----------------

def scrape_greenhouse(url: str):
    token = first_path_token(url)

    api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

    r = requests.get(api, headers=HEADERS, timeout=30)
    r.raise_for_status()

    data = r.json()
    jobs = []

    for job in data.get("jobs", []):

        # ---------- Location ----------
        location = ", ".join(
            o.get("name", "")
            for o in (job.get("offices") or [])
            if o.get("name")
        )

        if not location:
            location = job.get("location", {}).get("name", "")

        # ---------- Workplace Type ----------
        workplace_type = ""

        for meta in (job.get("metadata") or []):
            meta_name = meta.get("name", "").strip().lower()

            if meta_name in [
                "location type",
                "workplace type",
                "workplace",
            ]:
                workplace_type = meta.get("value", "")
                break

        # ---------- Job Type ----------
        job_type = (
            job.get("employment_type")
            or job.get("job_type")
            or job.get("commitment")
            or ""
        )

        # lightweight metadata fallback only
        if not job_type:
            for meta in (job.get("metadata") or []):
                meta_name = meta.get("name", "").strip().lower()

                if meta_name in [
                    "employment type",
                    "job type",
                    "commitment",
                    "worker type",
                ]:
                    job_type = meta.get("value", "")
                    break

        # ---------- Posted ----------
        posted = (
            job.get("first_published")
            or job.get("updated_at")
            or ""
        )

        jobs.append({
            "job_title": job.get("title", "").strip(),
            "company": job.get("company_name", token),
            "location": location,
            "job_type": job_type,
            "workplace_type": workplace_type,
            "posted": posted,
            "source_url": clean_url(job.get("absolute_url", "")),
        })

    return jobs


def scrape_ashby(url: str):
    token = first_path_token(url)

    api = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"

    r = requests.get(api, headers=HEADERS, timeout=30)
    r.raise_for_status()

    data = r.json()
    jobs = []

    for job in data.get("jobs", []):

        # ---------- Location ----------
        location = ""

        if isinstance(job.get("location"), dict):
            location = (
                job["location"].get("locationName", "")
                or job["location"].get("name", "")
            )

        elif isinstance(job.get("location"), str):
            location = job["location"]

        # ---------- Workplace Type ----------
        workplace_type = (
            job.get("workplaceType", "")
            or ("Remote" if job.get("isRemote") else "")
        )

        # ---------- Job Type ----------
        raw_job_type = str(job.get("employmentType", "")).strip()

        employment_mapping = {
            "FullTime": "Full-time",
            "PartTime": "Part-time",
            "Contract": "Contract",
            "Temporary": "Temporary",
            "Internship": "Internship",
            "FTC": "FTC",
            "FixedTerm": "FTC",
            "Permanent": "Permanent",
        }

        job_type = employment_mapping.get(
            raw_job_type,
            raw_job_type
        )

        # ---------- Posted ----------
        posted = (
            job.get("publishedAt")
            or job.get("publishedDate")
            or job.get("createdAt")
            or ""
        )

        jobs.append({
            "job_title": job.get("title", "").strip(),
            "company": data.get("name", token),
            "location": location,
            "job_type": job_type,
            "workplace_type": workplace_type,
            "posted": posted,
            "source_url": clean_url(job.get("jobUrl", "")),
        })

    return jobs


def scrape_smartrecruiters(url: str):

    company = first_path_token(url)

    limit = 100
    max_jobs = 1000
    offset = 0

    jobs = []

    while offset < max_jobs:

        api = (
            f"https://api.smartrecruiters.com/v1/companies/"
            f"{company}/postings?limit={limit}&offset={offset}"
        )

        r = requests.get(
            api,
            headers={
                "Accept": "application/json",
                **HEADERS
            },
            timeout=30,
        )

        r.raise_for_status()

        data = r.json()

        postings = data.get(
            "content",
            data.get("postings", data.get("jobs", []))
        )

        if isinstance(postings, dict):
            postings = postings.get("content", [])

        # no more jobs
        if not postings:
            break

        for job in postings:

            # stop at 1000
            if len(jobs) >= max_jobs:
                break

            # ---------- Location ----------
            location = ""

            if isinstance(job.get("location"), dict):
                location = (
                    job["location"].get("fullLocation")
                    or job["location"].get("city")
                    or ""
                )

            elif isinstance(job.get("location"), str):
                location = job["location"]

            # ---------- Workplace Type ----------
            workplace_type = ""

            if isinstance(job.get("location"), dict):

                if job["location"].get("hybrid"):
                    workplace_type = "Hybrid"

                elif job["location"].get("remote"):
                    workplace_type = "Remote"

                else:
                    workplace_type = "On-site"

            # fallback from custom fields
            if not workplace_type:

                for field in job.get("customField", []):

                    label = field.get("fieldLabel", "").strip().lower()

                    if label in [
                        "role type",
                        "workplace type",
                        "work location",
                    ]:
                        workplace_type = field.get("valueLabel", "")
                        break

            # ---------- Job Type ----------
            job_type = ""

            employment = job.get("typeOfEmployment")

            if isinstance(employment, dict):
                job_type = employment.get("label", "")

            elif isinstance(employment, str):
                job_type = employment

            # ---------- Posted ----------
            posted = (
                job.get("releasedDate")
                or job.get("postingDate")
                or ""
            )

            # ---------- URL ----------
            source_url = ""

            if str(job.get("ref", "")).startswith("http"):
                source_url = job.get("ref", "")

            else:
                source_url = clean_url(job.get("url", ""))

            jobs.append({
                "job_title": job.get("name", job.get("title", "")).strip(),
                "company": (
                    job.get("company", {}).get("name", company)
                    if isinstance(job.get("company"), dict)
                    else company
                ),
                "location": location,
                "job_type": job_type,
                "workplace_type": workplace_type,
                "posted": posted,
                "source_url": source_url,
            })

        # next page
        offset += limit

        total_found = data.get("totalFound", 0)

        if offset >= total_found:
            break

    return jobs


def scrape_workable(url: str):

    token = first_path_token(url)

    api = f"https://apply.workable.com/api/v1/widget/accounts/{token}"

    r = requests.get(api, headers=HEADERS, timeout=30)
    r.raise_for_status()

    data = r.json()

    jobs_raw = data.get("jobs", data.get("results", []))

    jobs = []

    for job in jobs_raw:

        # ---------- Location ----------
        location = ""

        if job.get("locations"):

            locations = []

            for loc in job.get("locations", []):

                city = loc.get("city", "")
                region = loc.get("region", "")
                country = loc.get("country", "")

                parts = [p for p in [city, region, country] if p]

                if parts:
                    locations.append(", ".join(parts))

            location = " | ".join(locations)

        else:

            parts = [
                job.get("city", ""),
                job.get("state", ""),
                job.get("country", ""),
            ]

            location = ", ".join([p for p in parts if p])

        # ---------- Workplace Type ----------
        workplace_type = ""

        if job.get("telecommuting") is True:
            workplace_type = "Remote"

        elif "hybrid" in str(location).lower():
            workplace_type = "Hybrid"

        else:
            workplace_type = "On-site"

        # ---------- Job Type ----------
        job_type = job.get("employment_type", "")

        # ---------- Posted ----------
        posted = (
            job.get("published_on")
            or job.get("created_at")
            or ""
        )

        jobs.append({
            "job_title": job.get("title", "").strip(),
            "company": token.replace("-", " ").title(),
            "location": location,
            "job_type": job_type,
            "workplace_type": workplace_type,
            "posted": posted,
            "source_url": clean_url(job.get("url", "")),
        })

    return jobs


def scrape_lever(url: str):

    token = first_path_token(url) or hostname_subdomain(url)

    api = f"https://api.lever.co/v0/postings/{token}?mode=json"

    r = requests.get(api, headers=HEADERS, timeout=30)
    r.raise_for_status()

    data = r.json()

    jobs = []

    for job in data:

        cats = job.get("categories", {}) or {}

        # ---------- Location ----------
        location = (
            cats.get("location", "")
            or ", ".join(cats.get("allLocations", []))
        )

        # ---------- Workplace Type ----------
        workplace_type = (
            job.get("workplaceType", "")
            or cats.get("workplaceType", "")
            or ""
        )

        # normalize
        if workplace_type:
            workplace_type = workplace_type.replace("-", " ").title()

        # ---------- Job Type ----------
        job_type = (
            cats.get("commitment", "")
            or ""
        )

        # ---------- Posted ----------
        posted = ""

        created_at = job.get("createdAt")

        if created_at:
            try:
                # lever returns milliseconds timestamp
                posted = datetime.utcfromtimestamp(
                    int(created_at) / 1000
                ).isoformat() + "Z"
            except Exception:
                posted = str(created_at)

        # ---------- URL ----------
        source_url = clean_url(
            job.get("hostedUrl", "")
            or job.get("applyUrl", "")
        )

        jobs.append({
            "job_title": job.get("text", "").strip(),
            "company": token.replace("-", " ").title(),
            "location": location,
            "job_type": job_type,
            "workplace_type": workplace_type,
            "posted": posted,
            "source_url": source_url,
        })

    return jobs



def scrape_personio(url: str):
    host = urlparse(url).hostname or ""

    api = f"https://{host}/xml?language=en"

    r = requests.get(api, headers=HEADERS, timeout=30)
    r.raise_for_status()

    root = ET.fromstring(r.text)

    jobs = []

    for job in root.findall(".//position"):

        # ---------- Location ----------
        office = job.findtext("office", default="").strip()

        # ---------- Workplace Type ----------
        if "remote" in office.lower():
            workplace_type = "Remote"
        else:
            workplace_type = ""

        # ---------- Job Type ----------
        recruiting_category = job.findtext(
            "recruitingCategory",
            default=""
        ).strip()

        employment_mapping = {
            "Permanent Employee": "Permanent",
            "Freelance": "Contract",
            "Intern": "Internship",
            "Working Student": "Part-time",
        }

        job_type = employment_mapping.get(
            recruiting_category,
            recruiting_category
        )

        # ---------- Job ID ----------
        job_id = job.findtext("id", default="").strip()

        # ---------- Source URL ----------
        source_url = (
            f"https://{host}/job/{job_id}"
            if job_id else ""
        )

        jobs.append({
            "job_title": job.findtext("name", default="").strip(),
            "company": job.findtext(
                "subcompany",
                default=host.split(".")[0]
            ).strip(),
            "location": office,
            "job_type": job_type,
            "workplace_type": workplace_type,
            "posted": job.findtext(
                "createdAt",
                default=""
            ).strip(),
            "source_url": source_url,
        })

    return jobs


def scrape_rippling(url: str):

    def extract_rippling_company(url):
        path_parts = [p for p in urlparse(url).path.split("/") if p]

        # remove locale prefix like en-GB
        if path_parts and "-" in path_parts[0]:
            path_parts = path_parts[1:]

        return path_parts[0] if path_parts else ""

    company_name = extract_rippling_company(url)

    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []

    data = json.loads(script.string)

    queries = (
        data.get("props", {})
        .get("pageProps", {})
        .get("dehydratedState", {})
        .get("queries", [])
    )

    items = []

    for q in queries:
        payload = q.get("state", {}).get("data", {})

        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            cand = payload.get("items", [])

            if (
                cand
                and isinstance(cand[0], dict)
                and "url" in cand[0]
                and ("name" in cand[0] or "title" in cand[0])
            ):
                items = cand
                break

    jobs = []

    for job in items:
        locs = job.get("locations", []) or []

        # employment type if available
        job_type = (
            job.get("employmentType")
            or job.get("employment_type")
            or ""
        )

        jobs.append({
            "job_title": job.get("name", job.get("title", "")),
            "company": company_name,
            "location": ", ".join(
                x.get("name", "") for x in locs if x.get("name")
            ),
            "job_type": job_type,
            "workplace_type": ", ".join(
                sorted(set(
                    x.get("workplaceType", "")
                    for x in locs
                    if x.get("workplaceType")
                ))
            ),
            "posted": "",
            "source_url": clean_url(job.get("url", "")),
        })

    return jobs


def scrape_salesforce(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []

    # Extract page-level timestamp
    posted = ""
    vs = soup.find("input", {"id": "com.salesforce.visualforce.ViewStateVersion"})

    if vs:
        raw = vs.get("value", "").strip()

        # Example: 202605222009430000
        if len(raw) >= 14:
            try:
                dt = datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
                posted = dt.isoformat()
            except Exception:
                posted = raw

    # Parse job rows
    for row in soup.select("table.jobListPanel tbody tr"):
        cols = row.find_all("td")

        if len(cols) < 7:
            continue

        # Job title + URL
        a = cols[0].find("a")
        if not a:
            continue

        job_title = a.get_text(strip=True)
        source_url = urljoin(url, a.get("href", ""))

        # Other fields
        job_type = cols[2].get_text(strip=True)
        location = cols[4].get_text(strip=True)
        company = cols[5].get_text(strip=True)

        # Workplace type
        workplace_type = ""
        if "remote" in location.lower():
            workplace_type = "Remote"
        elif "hybrid" in location.lower():
            workplace_type = "Hybrid"
        elif location:
            workplace_type = "On-site"

        jobs.append({
            "job_title": job_title,
            "company": company,
            "location": location,
            "job_type": job_type,
            "workplace_type": workplace_type,
            "posted": posted,
            "source_url": source_url,
        })

    # Deduplicate
    seen = set()
    out = []

    for j in jobs:
        key = (j["job_title"], j["source_url"])

        if key not in seen:
            seen.add(key)
            out.append(j)

    return out


def scrape_charliehr(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # company from subdomain
    company = urlparse(url).netloc.split(".")[0]

    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            data = json.loads(script.string)
            page_props = data.get("props", {}).get("pageProps", {})
            job_data = page_props.get("job_data", {})
            jobs_raw = job_data.get("jobs", [])

            out = []

            for job in jobs_raw:
                title = str(job.get("job_title", "")).strip()
                slug = str(job.get("slug", "")).strip()

                if not title:
                    continue

                location = (
                    job.get("city_country", "")
                    or job.get("remote_hyrbid_onsite_text", "")
                )

                workplace_type = str(
                    job.get("remote_hyrbid_onsite_text", "")
                ).strip()

                job_type = str(
                    job.get("contract_type", "")
                ).replace("_", " ").title()

                posted_raw = str(job.get("published_date", "")).strip()

                # default = today
                posted = datetime.today().strftime("%Y-%m-%d")

                # handle "Posted 13 day(s) ago"
                m = re.search(r"Posted\s+(\d+)\s+day", posted_raw, re.I)
                if m:
                    days = int(m.group(1))
                    posted = (
                        datetime.today() - timedelta(days=days)
                    ).strftime("%Y-%m-%d")

                job_url = (
                    urljoin(url.rstrip("/") + "/", slug)
                    if slug else url
                )

                out.append({
                    "job_title": title,
                    "company": company,
                    "location": str(location).strip(" ,"),
                    "job_type": job_type,
                    "workplace_type": workplace_type,
                    "posted": posted,
                    "source_url": job_url,
                })

            if out:
                return out

        except Exception:
            pass

    return []


def scrape_teamtailor(url: str):
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    company = host.split(".")[0]

    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    default_posted = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    jobs = []

    for a in soup.select('a[href*="/jobs/"]'):
        href = urljoin(url, a.get("href", ""))

        title_el = a.select_one('span[title]')
        job_title = title_el.get("title", "").strip() if title_el else ""

        meta_div = title_el.find_next("div") if title_el else None
        meta = []
        if meta_div:
            for child in meta_div.find_all("span", recursive=False):
                txt = child.get_text(" ", strip=True)
                if txt and txt != "·":
                    meta.append(txt)

        location = ""
        workplace_type = ""

        if len(meta) == 3:
            location = meta[1]
            workplace_type = meta[2]
        elif len(meta) == 2:
            location = meta[0]
            workplace_type = meta[1]
        elif len(meta) == 1:
            location = meta[0]

        jobs.append({
            "job_title": job_title,
            "company": company,
            "location": location,
            "job_type": "",
            "workplace_type": workplace_type,
            "posted": default_posted,
            "source_url": href,
        })

    seen = set()
    out = []
    for j in jobs:
        key = (j["job_title"], j["source_url"])
        if key not in seen:
            seen.add(key)
            out.append(j)

    return out


def scrape_apple(url: str):
    company = "Apple"
    jobs = []
    seen = set()
    page = 1

    while True:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query["page"] = [str(page)]

        page_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query, doseq=True),
            parsed.fragment
        ))

        r = requests.get(page_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        cards = soup.select("div.rc-accordion-button")
        if not cards:
            break

        page_jobs = []

        for card in cards:
            link = card.select_one('h3 a[href*="/details/"]')
            if not link:
                continue

            job_url = urljoin(page_url, link.get("href", "").strip())
            title = link.get_text(" ", strip=True)

            location_el = card.select_one(".job-title-location span:not(.a11y)")
            posted_el = card.select_one("span.job-posted-date")

            location = location_el.get_text(" ", strip=True) if location_el else ""
            posted_raw = posted_el.get_text(" ", strip=True) if posted_el else ""

            posted = ""
            if posted_raw:
                try:
                    posted = datetime.strptime(posted_raw, "%B %d, %Y").strftime("%Y-%m-%d")
                except ValueError:
                    posted = posted_raw

            key = job_url
            if key in seen:
                continue
            seen.add(key)

            page_jobs.append({
                "job_title": title,
                "company": company,
                "location": str(location).strip(" ,"),
                "job_type": "",
                "workplace_type": "",
                "posted": posted,
                "source_url": job_url,
            })

        if not page_jobs:
            break

        jobs.extend(page_jobs)
        page += 1

    return jobs


def scrape_avature(url: str):
    company = urlparse(url).netloc.split(".")[0]
    jobs = []
    seen = set()
    visited_pages = set()
    page_url = url
    page_num = 1

    while page_url and page_url not in visited_pages:
        visited_pages.add(page_url)

        r = requests.get(page_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        cards = soup.select("article.article--result")
        if not cards:
            break

        found_on_page = 0

        for card in cards:
            link = card.select_one('h3 a[href*="/JobDetail/"], h3 a[href*="/careers/JobDetail"], h3 a[href*="job/"]')
            if not link:
                continue

            job_url = urljoin(page_url, link.get("href", "").strip())
            title = link.get_text(" ", strip=True)

            location_el = card.select_one("span.list-item-location")
            location = location_el.get_text(" ", strip=True) if location_el else ""

            if not title or job_url in seen:
                continue

            seen.add(job_url)
            found_on_page += 1

            jobs.append({
                "job_title": title,
                "company": company,
                "location": location,
                "job_type": "",
                "workplace_type": "",
                "posted": "",
                "source_url": job_url,
            })

        if found_on_page == 0:
            break

        next_link = None
        for a in soup.select("a[href]"):
            txt = a.get_text(" ", strip=True).lower()
            href = a.get("href", "").strip()
            if "next" in txt and href:
                next_link = urljoin(page_url, href)
                break

        if next_link and next_link not in visited_pages:
            page_url = next_link
            continue

        page_num += 1
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query["page"] = [str(page_num)]
        fallback_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query, doseq=True),
            parsed.fragment
        ))

        if fallback_url in visited_pages:
            break

        page_url = fallback_url

    return jobs


def scrape_generic(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    fallback_company = normalize_company_name(hostname_subdomain(url))
    jobs = _extract_jobs_from_jsonld(soup, fallback_company=fallback_company)
    if jobs:
        return sort_jobs_by_posted(_dedupe_jobs(jobs))

    jobs = []
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(k in href for k in ["/job/", "/jobs/", "/careers/", "/vacancy/", "jobdetail", "job-details"]):
            title = safe_text(a)
            if title and len(title) > 3:
                jobs.append(_make_job(
                    job_title=title,
                    company=fallback_company or "Unknown",
                    location="",
                    job_type="",
                    workplace_type="",
                    posted="",
                    source_url=urljoin(url, a["href"]),
                ))

    return _dedupe_jobs(jobs)


@router.post("/scrape-ats", response_model=ATSScrapeResponse)
async def scrape_ats(request: ATSScrapeRequest):
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
        elif ats == "charliehr":
            jobs_raw = scrape_charliehr(url)
        elif ats == "rippling":
            jobs_raw = scrape_rippling(url)
        elif ats == "salesforce":
            jobs_raw = scrape_salesforce(url)
        elif ats == "teamtailor":
            jobs_raw = scrape_teamtailor(url)
        elif ats == "apple":
            jobs_raw = scrape_apple(url)
        elif ats == "avature":
            jobs_raw = scrape_avature(url)
        else:
            jobs_raw = scrape_generic(url)

        jobs_raw = [enrich_job_fields(j) for j in jobs_raw]
        jobs_raw = sort_jobs_by_posted(_dedupe_jobs(jobs_raw))

        jobs = [
            JobListing(
                job_title=j.get("job_title", "Unknown Title"),
                company=j.get("company", "Unknown Company"),
                location=j.get("location", ""),
                job_type=j.get("job_type", ""),
                workplace_type=j.get("workplace_type", ""),
                posted=j.get("posted", ""),
                source_url=j.get("source_url", "")
            )
            for j in jobs_raw
        ]

        return ATSScrapeResponse(jobs=jobs, ats_detected=ats)

    except Exception as e:
        logger.error(f"Scrape failed for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")