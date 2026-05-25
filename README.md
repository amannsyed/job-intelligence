# Job Intelligence

Job Intelligence is a FastAPI-based backend server designed to act as the LLM and scraping engine for the Resume Matcher. It provides a robust, multi-provider LLM router with automatic fallback, and a resilient job description scraper.

## Features

- **Multi-Provider LLM Router (`/api/llm/chat`)**: 
  - Uses [LiteLLM](https://github.com/BerriAI/litellm) to abstract API calls to various AI providers.
  - Implements an automatic fallback chain. If one provider fails or times out, it seamlessly falls back to the next available provider for the selected model.
  - Configured models include: DeepSeek V4 Pro, DeepSeek V4 Flash, GLM 5.1, GLM 5, Qwen3.6-35B-A3B, and Kimi K2.6.
  - Providers supported out-of-the-box: NVIDIA NIM, HuggingFace Inference API, OpenRouter, and Cloudflare Workers AI.

- **Resilient Job Description Scraper (`/api/fetch-jd`)**:
  - Extracts clean job description text from URLs using a multi-strategy approach:
    1. **Jina AI Reader**: Bypasses JS-heavy pages and returns clean markdown.
    2. **JSON-LD Schema Extraction**: Directly parses `JobPosting` structured data.
    3. **DOM Parsing Fallback**: Uses BeautifulSoup to intelligently extract main content and strip noisy elements (navs, footers, cookie banners).

## Prerequisites

- Python 3.12+
- API keys for your preferred LLM providers (NVIDIA, HuggingFace, OpenRouter, Cloudflare).

## Installation

1. Clone the repository and navigate to the project directory.
2. Create and activate a virtual environment (recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows, use `.venv\Scripts\activate`
   ```
3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Create a `.env` file in the root directory (you can use `.env.example` as a template if available) and add your API keys. The application automatically maps common environment variables to the ones expected by LiteLLM:

```env
# LLM Provider API Keys
NVIDIA_API_KEY="your-nvidia-api-key"
# or NVIDIA_NIM_API_KEY="your-nvidia-api-key"

HF_API_KEY="your-huggingface-api-key"
# or HUGGING_FACE_API_KEY="your-huggingface-api-key"

OPENROUTER_API_KEY="your-openrouter-api-key"

CLOUDFLARE_AUTH_TOKEN="your-cloudflare-token"
# or CLOUDFLARE_API_KEY="your-cloudflare-token"

# CORS Configuration
ALLOWED_ORIGINS="http://localhost:3000,http://localhost:8080"
```

## Running the Server

Start the development server using `uvicorn`:

```bash
uvicorn main:app --port 8000 --reload
```

The API will be available at `http://localhost:8000`.

## API Endpoints

Once running, you can access the interactive API documentation (Swagger UI) at `http://localhost:8000/docs`.

### Core Endpoints

- `GET /` - Service status check.
- `GET /api/llm/health` - LLM router health check.
- `GET /api/llm/models` - List all configured models and their available providers.
- `POST /api/llm/chat` - Chat completion endpoint. Supports standard chat messages and optional JSON response formatting.
- `POST /api/fetch-jd` - Job description scraping endpoint. Pass a JSON body with `{"url": "https://..."}`.

## Project Structure

- `main.py`: Application entry point, FastAPI setup, and CORS configuration.
- `llm_router.py`: LLM routing logic, endpoint definitions, and fallback mechanics.
- `models_config.py`: Configuration file defining the `MODEL_CHAIN` and priority lists for providers.
- `scraper.py`: URL scraping logic implementing the multi-strategy extraction approach.
