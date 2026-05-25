"""
Job description URL scraper.

Provides a POST /api/fetch-jd endpoint that extracts job description text
from a given URL using multiple strategies:
1. Jina AI Reader (clean markdown, bypasses JS-heavy pages)
2. Direct fetch with JSON-LD JobPosting schema extraction
3. Fallback to DOM text parsing via BeautifulSoup
"""

import json
import logging
import re

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("scraper")
router = APIRouter(prefix="/api")

# Shared HTTP client settings
CHROME_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

CURL_HEADERS = {
    "User-Agent": "curl/7.68.0",
    "Accept": "*/*",
}


class FetchJDRequest(BaseModel):
    url: str


class FetchJDResponse(BaseModel):
    text: str


def clean_url(raw_url: str) -> str:
    """Extract and clean a URL from potentially messy user input."""
    match = re.search(r"https?://[^\s\"']+", raw_url)
    if match:
        return match.group(0).strip()
    return raw_url.strip()


def find_job_posting(obj) -> dict | None:
    """Recursively search JSON-LD data for a JobPosting schema object."""
    if not obj:
        return None
    if isinstance(obj, dict):
        if obj.get("@type") == "JobPosting":
            return obj
        if "@graph" in obj:
            return find_job_posting(obj["@graph"])
    if isinstance(obj, list):
        for item in obj:
            found = find_job_posting(item)
            if found:
                return found
    return None


async def try_jina_reader(url: str) -> str | None:
    """Strategy 1: Use Jina AI Reader for clean markdown extraction."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                jina_url,
                headers={
                    "Accept": "text/plain",
                    "User-Agent": "Resume-Matcher-Bot/1.0",
                },
            )
            if response.is_success:
                markdown = response.text
                # Verify it didn't just grab a cookie consent wall
                if (
                    len(markdown) > 200
                    and "GSK values your privacy" not in markdown
                    and "Cookie Policy" not in markdown
                ):
                    return markdown
    except Exception as e:
        logger.warning(f"Jina AI reader failed: {e}")
    return None


async def try_direct_fetch(url: str) -> str | None:
    """Strategy 2 & 3: Direct fetch with JSON-LD extraction, then DOM fallback."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, headers=CHROME_HEADERS)

            # Secondary fallback if WAF blocks Chrome User-Agent
            if response.status_code == 400:
                response = await client.get(url, headers=CURL_HEADERS)

            if not response.is_success:
                raise Exception(f"Failed to load page. Status: {response.status_code}")

            html = response.text
            soup = BeautifulSoup(html, "html.parser")
            extracted_text = ""

            # Strategy 2: Try extracting JobPosting JSON-LD schema
            for script_tag in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script_tag.string or "")
                    job_data = find_job_posting(data)
                    if job_data and job_data.get("description"):
                        clean_desc = BeautifulSoup(
                            job_data["description"], "html.parser"
                        ).get_text(separator=" ", strip=True)
                        title = job_data.get("title", "")
                        extracted_text = (
                            f"{title}\n\n{clean_desc}" if title else clean_desc
                        )
                        break
                except (json.JSONDecodeError, Exception):
                    continue

            # Strategy 3: Fallback to DOM parsing if JSON-LD failed
            if not extracted_text or len(extracted_text) < 150:
                # Remove noisy elements and cookie banners
                for tag in soup.find_all(
                    ["script", "style", "nav", "footer", "header", "noscript", "svg", "button"]
                ):
                    tag.decompose()

                # Remove cookie/consent banners by id/class patterns
                for el in soup.find_all(
                    attrs={"id": re.compile(r"cookie|consent", re.I)}
                ):
                    el.decompose()
                for el in soup.find_all(
                    attrs={"class": re.compile(r"cookie|consent", re.I)}
                ):
                    el.decompose()

                # Try main content zones first
                main_content = ""
                for selector in ["main", "[role='main']", "#main-content", ".job-description", ".description"]:
                    el = soup.select_one(selector)
                    if el:
                        text = re.sub(r"\s+", " ", el.get_text(strip=True))
                        if len(text) > 200:
                            main_content = text
                            break

                if main_content:
                    extracted_text = main_content
                else:
                    body = soup.find("body")
                    if body:
                        extracted_text = re.sub(r"\s+", " ", body.get_text(strip=True))

            return extracted_text if extracted_text else None

    except Exception as e:
        logger.warning(f"Direct fetch failed: {e}")
        return None


@router.post("/fetch-jd", response_model=FetchJDResponse)
async def fetch_jd(request: FetchJDRequest):
    """
    Extract job description text from a URL.

    Tries multiple strategies in order:
    1. Jina AI Reader (markdown, JS-capable)
    2. Direct fetch + JSON-LD JobPosting schema
    3. Direct fetch + DOM text extraction
    """
    url = clean_url(request.url)
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    logger.info(f"Scraping JD from: {url}")

    # Strategy 1: Jina AI Reader
    text = await try_jina_reader(url)

    # Strategy 2 & 3: Direct fetch
    if not text:
        text = await try_direct_fetch(url)

    if not text or len(text) < 150:
        raise HTTPException(
            status_code=500,
            detail="Extracted text is too short, likely blocked by anti-bot measures. Please paste manually.",
        )

    logger.info(f"Successfully extracted {len(text)} chars from {url}")
    return FetchJDResponse(text=text)
