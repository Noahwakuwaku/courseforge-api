"""
Shared client / DB / helper utilities.

Key changes vs. v1:
  * AsyncOpenAI client is built ONCE and reused (was rebuilt per call).
  * chat() is gated by an asyncio.Semaphore to cap concurrent LLM calls
    regardless of how many worker jobs are running — this protects against
    the provider's RPM/TPM rate limits under high-concurrency generation.
  * chat() retries on transient errors (429 / 5xx / network) with
    exponential backoff. Retries are bounded by settings.LLM_MAX_RETRIES.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

import httpx
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from config import settings


# ── OpenAI client (singleton + bounded concurrency) ───────────────────────────
_openai_client: AsyncOpenAI | None = None
_llm_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy-init so we attach to the running event loop, not import-time."""
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
    return _llm_semaphore


def get_openai_client() -> AsyncOpenAI:
    """Singleton AsyncOpenAI client.

    Reusing a single client preserves the underlying httpx connection pool
    — without this, every chat() call paid the cost of TLS handshake + new
    connection. Under 30+ concurrent jobs that's a real bottleneck.
    """
    global _openai_client
    if _openai_client is None:
        # httpx limits chosen to comfortably exceed LLM_MAX_CONCURRENCY so the
        # semaphore is always the binding constraint, not the connection pool.
        limits = httpx.Limits(
            max_connections=max(64, settings.LLM_MAX_CONCURRENCY * 3),
            max_keepalive_connections=max(32, settings.LLM_MAX_CONCURRENCY * 2),
        )
        http_client = httpx.AsyncClient(
            limits=limits,
            timeout=httpx.Timeout(settings.LLM_TIMEOUT, connect=15.0),
        )
        _openai_client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            http_client=http_client,
            max_retries=0,  # we do our own retry below for full control
        )
    return _openai_client


# Errors worth retrying. Everything else (e.g. auth, bad request) fails fast.
_RETRY_EXC = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    asyncio.TimeoutError,
    httpx.HTTPError,
)


async def chat(messages: list[dict], temperature: float = 0.7) -> str:
    """Call the chat completion endpoint with concurrency control + retries.

    The semaphore is acquired around the actual HTTP call, then released
    while sleeping between retries so a stuck job doesn't keep a slot busy.
    """
    sem = _get_semaphore()
    client = get_openai_client()

    last_exc: Exception | None = None
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            async with sem:
                resp = await client.chat.completions.create(
                    model=settings.MODEL_NAME,
                    messages=messages,
                    temperature=temperature,
                    # Qwen3 thinking mode — disable for structured output
                    extra_body={"enable_thinking": False},
                )
                return resp.choices[0].message.content

        except _RETRY_EXC as e:
            last_exc = e
            if attempt >= settings.LLM_MAX_RETRIES:
                break
            # Exponential backoff with full jitter
            base = settings.LLM_RETRY_BACKOFF * (2 ** attempt)
            await asyncio.sleep(base * (0.5 + random.random() * 0.5))

        except APIError as e:
            # 5xx is worth retrying; 4xx (bad input / auth) is not.
            last_exc = e
            status = getattr(e, "status_code", None)
            if status is None or status < 500 or attempt >= settings.LLM_MAX_RETRIES:
                raise
            base = settings.LLM_RETRY_BACKOFF * (2 ** attempt)
            await asyncio.sleep(base * (0.5 + random.random() * 0.5))

    # All retries exhausted
    assert last_exc is not None
    raise last_exc


# ── MongoDB ───────────────────────────────────────────────────────────────────
_mongo_client: AsyncIOMotorClient | None = None


def get_db():
    global _mongo_client
    if _mongo_client is None:
        # maxPoolSize default is 100 — plenty for our workload, but make
        # it explicit so behavior is predictable under heavy concurrency.
        _mongo_client = AsyncIOMotorClient(
            settings.MONGO_URI,
            maxPoolSize=100,
            minPoolSize=5,
        )
    return _mongo_client[settings.MONGO_DB]


# ── Helpers ───────────────────────────────────────────────────────────────────
def parse_json(text: str) -> dict | list:
    """Extract JSON from LLM output that may contain markdown fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def serialize_doc(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def serialize_docs(docs: list[dict]) -> list[dict]:
    return [serialize_doc(d) for d in docs]
