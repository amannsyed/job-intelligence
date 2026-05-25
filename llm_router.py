"""
LLM API router for the Resume Matcher backend.

Provides a single POST /chat endpoint that routes requests through
the model priority chain using LiteLLM, with automatic provider fallback.
"""

import logging
import time
from typing import Optional

import litellm
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from litellm import completion

from models_config import MODEL_CHAIN, MODEL_INDEX_MAP, DEFAULT_MODEL_INDEX

# Per-provider timeout in seconds
PROVIDER_TIMEOUT_SECONDS = 60

logger = logging.getLogger("llm-router")
router = APIRouter(prefix="/api/llm")


class ChatMessage(BaseModel):
    role: str = "user"
    content: str


class ChatRequest(BaseModel):
    """Request body for the /chat endpoint."""
    messages: list[ChatMessage]
    model_index: Optional[int] = Field(
        default=None,
        description="Index into MODEL_CHAIN to start from. If None, uses DEFAULT_MODEL_INDEX.",
    )
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=8192, ge=1, le=32768)
    response_format: Optional[str] = Field(
        default=None,
        description="Set to 'json' to request JSON output from the model.",
    )


class ChatResponse(BaseModel):
    """Response body from the /chat endpoint."""
    text: str
    model_used: str
    provider_used: str
    latency_ms: int


@router.get("/health")
def health():
    """Health check for the LLM router."""
    return {
        "status": "ok",
        "models": [
            {"index": i, "name": m["name"], "providers": len(m["providers"])}
            for i, m in enumerate(MODEL_CHAIN)
        ],
    }


@router.get("/models")
def list_models():
    """Return the full model chain with provider details."""
    return [
        {
            "index": i,
            "name": m["name"],
            "providers": m["providers"],
        }
        for i, m in enumerate(MODEL_CHAIN)
    ]


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a chat completion request through the model fallback chain.

    Tries each provider for the selected model first, then falls through
    to subsequent models in the chain until one succeeds.
    """
    start_index = request.model_index if request.model_index is not None else DEFAULT_MODEL_INDEX

    # Validate model index
    if start_index not in MODEL_INDEX_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model_index {start_index}. Must be 0-{len(MODEL_CHAIN) - 1}.",
        )

    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    # Build the ordered fallback chain starting from the selected model
    chain = MODEL_CHAIN[start_index:] + MODEL_CHAIN[:start_index]

    errors = []

    for model_entry in chain:
        model_name = model_entry["name"]

        for provider_model_id in model_entry["providers"]:
            t0 = time.time()

            try:
                logger.info(f"Trying {model_name} via {provider_model_id}")

                kwargs = {
                    "model": provider_model_id,
                    "messages": messages,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "timeout": PROVIDER_TIMEOUT_SECONDS,
                    "max_retries": 0,
                }

                # JSON mode: add response_format if supported
                if request.response_format == "json":
                    kwargs["response_format"] = {"type": "json_object"}

                response = completion(**kwargs)
                latency = int((time.time() - t0) * 1000)

                text = response.choices[0].message.content or ""

                logger.info(
                    f"Success: {model_name} via {provider_model_id} "
                    f"({latency}ms, {len(text)} chars)"
                )

                return ChatResponse(
                    text=text,
                    model_used=model_name,
                    provider_used=provider_model_id,
                    latency_ms=latency,
                )

            except Exception as e:
                latency = int((time.time() - t0) * 1000)
                error_msg = f"{provider_model_id}: {type(e).__name__}: {str(e)[:200]}"
                errors.append(error_msg)
                logger.warning(f"Failed {model_name} via {provider_model_id} ({latency}ms): {e}")
                continue

    # All models/providers exhausted
    logger.error(f"All models failed. Errors: {errors}")
    raise HTTPException(
        status_code=502,
        detail={
            "error": "All models and providers failed.",
            "attempts": errors,
        },
    )
