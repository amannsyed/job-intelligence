"""
Model priority configuration for the Resume Matcher LLM backend.

Each entry defines a model and an ordered list of provider-specific
LiteLLM model identifiers to try. The fallback chain iterates through
providers in order until one succeeds.

LiteLLM provider prefixes:
  - groq/         → Groq (uses GROQ_API_KEY)
  - nvidia_nim/   → NVIDIA NIM API (uses NVIDIA_NIM_API_KEY)
  - huggingface/  → HuggingFace Inference API (uses HUGGING_FACE_API_KEY)
  - openrouter/   → OpenRouter (uses OPENROUTER_API_KEY)
  - cloudflare/   → Cloudflare Workers AI (uses CLOUDFLARE_API_KEY)
  - mistral/      → Mistral API (uses MISTRAL_API_KEY)
"""

MODEL_CHAIN = [
    {
        "name": "DeepSeek V4 Flash",
        "providers": [
            "huggingface/deepseek-ai/DeepSeek-V4-Flash:cheapest",
        ],
    },
]

# Flat lookup: model_index -> MODEL_CHAIN entry
MODEL_INDEX_MAP = {i: m for i, m in enumerate(MODEL_CHAIN)}

# Default model index
DEFAULT_MODEL_INDEX = 0
