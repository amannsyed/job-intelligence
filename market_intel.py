import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from ddgs import DDGS
from litellm import completion

from models_config import MODEL_CHAIN, DEFAULT_MODEL_INDEX

logger = logging.getLogger("market-intel")
router = APIRouter(prefix="/api")

class MarketIntelRequest(BaseModel):
    company_name: str
    job_title: str

class MarketIntelResponse(BaseModel):
    market_intel: str
    source: str

def search_market_info(company_name: str, job_title: str) -> str:
    # Use queries that capture culture, benefits, and recent news
    queries = [
        f"{company_name} company culture reviews working environment",
        f"{company_name} {job_title} salary range benefits"
    ]
    results = []
    try:
        ddgs = DDGS()
        for query in queries:
            logger.info(f"Searching web: {query}")
            for r in ddgs.text(query, max_results=5):
                results.append(f"Source: {r.get('title')}\nSnippet: {r.get('body')}")
    except Exception as e:
        logger.error(f"DDGS Search failed: {e}")
        
    return "\n\n".join(results)

def summarize_market_intel(company_name: str, job_title: str, search_results: str) -> str:
    if not search_results.strip():
        return "No significant recent data found on the web."
        
    prompt = f"""
Based on the following live web search results, write a concise market intelligence summary for a candidate applying to {company_name} as a {job_title}.
Focus on:
1. Company Culture & Work Environment
2. Estimated Salary Range & Benefits (if mentioned)
3. Growth Opportunities or Recent Company News

Format the response as clean Markdown with headings or bullet points.
If the search results don't contain much info, just summarize what is available and mention that data is limited.

Search Results:
{search_results}
"""
    messages = [{"role": "user", "content": prompt}]
    
    # Try models down the fallback chain
    chain = MODEL_CHAIN[DEFAULT_MODEL_INDEX:] + MODEL_CHAIN[:DEFAULT_MODEL_INDEX]
    for model_entry in chain:
        for provider_model_id in model_entry["providers"]:
            try:
                kwargs = {
                    "model": provider_model_id,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 500,
                    "timeout": 60,
                    "max_retries": 0
                }
                response = completion(**kwargs)
                text = response.choices[0].message.content
                if text:
                    return text
            except Exception as e:
                logger.warning(f"Failed LLM for Market Intel via {provider_model_id}: {e}")
                continue
                
    return "Could not generate market intelligence from search results due to an LLM error."

@router.post("/market-intel", response_model=MarketIntelResponse)
async def get_market_intel(request: MarketIntelRequest):
    if not request.company_name or request.company_name.lower() == "unknown":
        return MarketIntelResponse(
            market_intel="Company name not provided or unknown. Unable to fetch live market data.", 
            source="None"
        )
        
    search_results = search_market_info(request.company_name, request.job_title)
    if not search_results:
        return MarketIntelResponse(
            market_intel="No recent web search results found for this company and role.", 
            source="Web Search"
        )
        
    summary = summarize_market_intel(request.company_name, request.job_title, search_results)
    return MarketIntelResponse(market_intel=summary, source="Live Web Search + LLM")
