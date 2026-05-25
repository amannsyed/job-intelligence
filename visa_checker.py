import csv
import io
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import List, Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from thefuzz import fuzz
from ddgs import DDGS
from litellm import completion

from models_config import MODEL_CHAIN, DEFAULT_MODEL_INDEX

logger = logging.getLogger("visa-checker")
router = APIRouter(prefix="/api")

GOV_UK_PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
CACHE_FILE = "sponsors_list.json"
CACHE_INFO_FILE = "sponsors_meta.json"
CACHE_DURATION_SEC = 3 * 3600  # 3 hours

class VisaCheckRequest(BaseModel):
    company_name: str
    country: str = "UK"

class VisaCheckResponse(BaseModel):
    sponsors_visa: str # "Yes", "No", "Maybe"
    reason: str
    source: str

def get_csv_url() -> str:
    response = requests.get(GOV_UK_PAGE)
    response.raise_for_status()
    match = re.search(r'href="(https://assets\.publishing\.service\.gov\.uk[^"]+\.csv)"', response.text)
    if match:
        return match.group(1)
    raise ValueError("Could not find CSV download URL on GOV.UK page")

def refresh_uk_sponsors_cache() -> List[dict]:
    csv_url = get_csv_url()
    logger.info(f"Fetching UK sponsors from: {csv_url}")
    
    response = requests.get(csv_url)
    response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))
    sponsors = []

    for row in reader:
        cleaned = {
            k.strip(): (v.strip() if v.strip().upper() != "NULL" else "")
            for k, v in row.items()
        }
        sponsors.append(cleaned)

    # Save to cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(sponsors, f, ensure_ascii=False)
    
    with open(CACHE_INFO_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_updated": time.time()}, f)
        
    return sponsors

def get_uk_sponsors() -> List[dict]:
    if os.path.exists(CACHE_FILE) and os.path.exists(CACHE_INFO_FILE):
        try:
            with open(CACHE_INFO_FILE, "r") as f:
                meta = json.load(f)
            if time.time() - meta.get("last_updated", 0) < CACHE_DURATION_SEC:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Error reading cache: {e}")
            
    return refresh_uk_sponsors_cache()

def check_uk_sponsor(company_name: str) -> bool:
    sponsors = get_uk_sponsors()
    target = company_name.lower()
    
    for sponsor in sponsors:
        org_name = sponsor.get("Organisation Name", "").lower()
        if not org_name:
            continue
            
        if org_name == target:
            return True
            
        if fuzz.ratio(target, org_name) >= 95:
            return True
            
    return False

def search_visa_info(company_name: str, country: str) -> str:
    current_year = datetime.now().year
    query = f"{company_name} {country} {current_year} Visa sponsorship license available?"
    logger.info(f"Searching web: {query}")
    
    results = []
    try:
        ddgs = DDGS()
        for r in ddgs.text(query, max_results=10):
            results.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}\nURL: {r.get('href')}")
    except Exception as e:
        logger.error(f"DDGS Search failed: {e}")
        return ""
        
    return "\n\n".join(results)

def ask_llm(company_name: str, country: str, search_results: str) -> dict:
    prompt = f"""
Based on the following web search results, does the company "{company_name}" in "{country}" offer visa sponsorship for workers?
Respond with a JSON object containing:
- "sponsors_visa": exactly "Yes", "No", or "Maybe"
- "reason": A brief 1-2 sentence explanation based on the search results.

Search Results:
{search_results}
"""
    messages = [{"role": "user", "content": prompt}]
    
    # Use the configured model chain (similar to llm_router)
    chain = MODEL_CHAIN[DEFAULT_MODEL_INDEX:] + MODEL_CHAIN[:DEFAULT_MODEL_INDEX]
    for model_entry in chain:
        for provider_model_id in model_entry["providers"]:
            try:
                kwargs = {
                    "model": provider_model_id,
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 150,
                    "timeout": 60,
                    "max_retries": 0,
                    "response_format": {"type": "json_object"}
                }
                response = completion(**kwargs)
                text = response.choices[0].message.content
                if text:
                    data = json.loads(text)
                    return data
            except Exception as e:
                logger.warning(f"Failed LLM for Visa Check via {provider_model_id}: {e}")
                continue
                
    return {"sponsors_visa": "Maybe", "reason": "Failed to analyze search results."}

@router.post("/check-visa", response_model=VisaCheckResponse)
async def check_visa(request: VisaCheckRequest):
    if request.country.lower() in ["uk", "united kingdom", "great britain"]:
        try:
            is_sponsor = check_uk_sponsor(request.company_name)
            if is_sponsor:
                return VisaCheckResponse(
                    sponsors_visa="Yes", 
                    reason="Found an exact or fuzzy match (>=95%) in the official GOV.UK Register of Licensed Sponsors.",
                    source="GOV.UK"
                )
        except Exception as e:
            logger.error(f"Error checking UK sponsor list: {e}")
            # Fall back to web search if fetching gov list fails
            
    # Web search fallback
    search_results = search_visa_info(request.company_name, request.country)
    if not search_results:
        return VisaCheckResponse(sponsors_visa="Maybe", reason="No search results found.", source="Web Search")
        
    llm_result = ask_llm(request.company_name, request.country, search_results)
    
    return VisaCheckResponse(
        sponsors_visa=llm_result.get("sponsors_visa", "Maybe"),
        reason=llm_result.get("reason", "Analysis completed."),
        source="Web Search + LLM"
    )
