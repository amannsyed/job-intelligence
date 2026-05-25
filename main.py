"""
Job Intelligence — LLM & Scraping Backend

A FastAPI server providing:
- Multi-provider LLM access via LiteLLM with automatic fallback
- Job description URL scraping with multiple extraction strategies

Usage:
    pip install -r requirements.txt
    uvicorn main:app --port 8000 --reload
"""

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import litellm

if os.getenv("HUGGING_FACE_API_KEY") and not os.getenv("HUGGINGFACE_API_KEY"):
    os.environ["HUGGINGFACE_API_KEY"] = os.environ["HUGGING_FACE_API_KEY"]

# Drop unsupported params silently (e.g. Cloudflare doesn't support temperature)
litellm.drop_params = True

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from llm_router import router as llm_router
from scraper import router as scraper_router
from visa_checker import router as visa_router
from market_intel import router as market_intel_router
from ats_scraper import router as ats_router

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("job-intelligence")

app = FastAPI(
    title="Job Intelligence API",
    description="Multi-provider LLM proxy and job description scraper for Resume Matcher",
    version="1.0.0",
)

# CORS — allow GitHub Pages production and local development
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").split(",") if os.getenv("ALLOWED_ORIGINS") else []
ALLOWED_ORIGINS.extend([
    "https://amannsyed.github.io",
    "http://localhost:3001",
    "http://localhost:5173",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:5173",
])

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(llm_router)
app.include_router(scraper_router)
app.include_router(visa_router)
app.include_router(market_intel_router)
app.include_router(ats_router)


@app.get("/")
@app.head("/")
def root():
    return {
        "service": "Job Intelligence API",
        "status": "running",
        "docs": "/docs",
        "endpoints": ["/api/llm/chat", "/api/llm/health", "/api/llm/models", "/api/fetch-jd", "/api/check-visa", "/api/market-intel", "/api/scrape-ats"],
    }
