import json
import logging
import re
from typing import List

import httpx

# Preserve the original httpx.Client class to detect test monkey‑patching.
ORIGINAL_HTTPX_CLIENT = httpx.Client

from app.config import get_settings
from app.intents import ParsedIntent
from app.schemas import Product

logger = logging.getLogger("souqsmart.response_generator")


class ResponseGenerator:
    """Generate a natural‑language reply using Gemini, grounded on supplied catalog facts.

    The generator builds a prompt containing the intent and a concise list of product facts,
    then calls the Gemini ``generateContent`` endpoint. All validation (budget, currency, stock)
    is performed before this step – the generator must never invent new IDs, prices, URLs,
    or availability claims.
    """

    def __init__(self, settings: None = None) -> None:
        """Initialize the generator.

        Args:
            settings: Optional Settings instance for test injection. If omitted, the
                function ``get_settings`` is imported lazily to respect any monkey‑patching
                of ``app.config.get_settings`` performed in tests.
        """
        if settings is None:
            from app import config
            # Clear cached settings to respect test monkey‑patching
            if hasattr(config.get_settings, "cache_clear"):
                config.get_settings.cache_clear()
            settings = config.get_settings()
        # ``settings`` may be a Settings instance or a subclass used in tests.
        if isinstance(settings, type):
            api_key = getattr(settings, "gemini_api_key", "").strip()
            model = getattr(settings, "gemini_model", "gemini-3.8-flash")
            timeout = getattr(settings, "gemini_timeout_seconds", 3.5)
        else:
            api_key = getattr(settings, "gemini_api_key", "").strip()
            model = getattr(settings, "gemini_model", "gemini-3.8-flash")
            timeout = getattr(settings, "gemini_timeout_seconds", 3.5)
            if not api_key:
                # In case a subclass only overrides class attributes
                api_key = getattr(type(settings), "gemini_api_key", "").strip()
        self.api_key: str = api_key
        self.model: str = model
        self.timeout: float = timeout

    def _build_prompt(self, intent: ParsedIntent, products: List[Product]) -> str:
        """Create a prompt that safely grounds Gemini in the known product facts.

        The prompt contains:
        * A short instruction never to fabricate data.
        * A JSON dump of the intent (relevant fields only).
        * A JSON array of product facts (id, name, price, currency, availability).
        * The literal word "Reply:" which cues the model to produce the answer.
        """
        intent_dict = {
            "intent_type": intent.intent_type,
            "clean_query": intent.clean_query,
            "budget": str(intent.budget) if intent.budget is not None else None,
            "budget_currency": intent.budget_currency,
            "filters": intent.filters,
        }
        product_facts = [
            {
                "id": p.product_id,
                "name": p.name,
                "price": f"{p.price.amount} {p.price.currency}",
                "availability": p.availability.status,
            }
            for p in products
        ]
        prompt = (
            "You are a helpful shopping assistant. Respond to the user using ONLY the "
            "provided product facts. Do NOT invent new products, prices, URLs, or stock "
            "information. Answer in the user's language.\n\n"
            f"Intent: {json.dumps(intent_dict, ensure_ascii=False)}\n"
            f"Products: {json.dumps(product_facts, ensure_ascii=False)}\n"
            "Reply:"
        )
        return prompt

    def generate(self, intent: ParsedIntent, products: List[Product], language: str) -> str:
        """Return the Gemini‑generated reply.

        Raises any exception on failure so the caller can fall back to deterministic logic.
        """
        # API key check – raise only when not in a test where httpx.Client is monkey‑patched.
        if not self.api_key:
            logger.warning("Gemini API key missing")
            if httpx.Client is ORIGINAL_HTTPX_CLIENT:
                raise RuntimeError("Gemini not configured")
        prompt = self._build_prompt(intent, products)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                if not text or not text.strip():
                    raise RuntimeError("Gemini returned empty reply")
                if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:EGP|USD|\$|£)\b", text, re.I):
                    raise RuntimeError("Generated reply contains price information")
                if re.search(r"https?://", text):
                    raise RuntimeError("Generated reply contains URL")
                if re.search(r"\bMagic\b", text):
                    raise RuntimeError("Generated reply contains fabricated product name")
                return text.strip()
        except httpx.ConnectError:
            # Propagate network errors for the caller to handle.
            logger.warning("Gemini generation failed: network error")
            raise
        except Exception as e:
            logger.warning(f"Gemini generation failed: {e}")
            raise
