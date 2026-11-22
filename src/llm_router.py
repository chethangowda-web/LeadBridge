"""Resilient multi-provider LLM router with strict Pydantic JSON enforcement.

Failover chain:
  0. Apify OpenRouter proxy (only inside an Apify run): anthropic/claude-haiku-5.5 by default.
     Billed to the user's own Apify credits through the run's APIFY_TOKEN, so a published
     Actor never spends the developer's personal LLM keys.
  1. Google AI Studio: gemini-3.5-flash-lite
  2. Google AI Studio: gemini-3.1-flash-lite (secondary Gemini quota)
  3. Anthropic Claude: CLAUDE_MODEL env (default claude-opus-5-5), native structured outputs
  4. OpenRouter Free:  openrouter/free
Tiers 1-4 run only when their key is configured (typically local development).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TypeVar

from anthropic import AsyncAnthropic
from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
# Server-side refusal fallback is only accepted by these models (not Haiku 5.5).
CLAUDE_FALLBACK_CAPABLE = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}

CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium")

APIFY_OPENROUTER_BASE_URL = "https://openrouter.apify.actor/api/v1"
# Through the proxy, thinking tokens count against the output cap; on the first platform runs
# Haiku's thinking used it up and cut the JSON off (finish_reason=length). Keep thinking small.
PROXY_REASONING_EFFORT = os.getenv("PROXY_REASONING_EFFORT", "low")
DEFAULT_APIFY_PROXY_MODEL = "anthropic/claude-haiku-5.5"
PROXY_MAX_TOKENS = 16000
# Measured on live runs: replies through the Apify proxy stop at exactly 2048 completion tokens
# (thinking included), whatever max_tokens we send. Batch sizes must fit under this.
PROXY_OUTPUT_TOKEN_CAP = 2048
DIRECT_OUTPUT_TOKEN_CAP = 8000
# Room for thinking tokens and the JSON wrapper inside each reply. Through the proxy, Haiku's
# thinking used 690-900 of the 2048 tokens on live Stage 4 batches, so reserve ~1000 there.
PROXY_OUTPUT_OVERHEAD_TOKENS = 1000
DIRECT_OUTPUT_OVERHEAD_TOKENS = 350
# Claude runs with adaptive thinking, so allow more headroom than the 30s
# budget used for the lightweight free-tier providers.
CLAUDE_MIN_TIMEOUT_SECONDS = 120.0


class LLMRouterError(RuntimeError):
    """Raised when every configured LLM provider fails."""


def _extract_json_block(raw_text: str) -> str:
    """Strip markdown code fences or prose around a JSON object/array."""
    text = raw_text.strip()
    # Strip ```json ... ``` fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    # Locate outer JSON object or array boundaries
    obj_start, obj_end = text.find("{"), text.rfind("}")
    arr_start, arr_end = text.find("["), text.rfind("]")

    if obj_start != -1 and (arr_start == -1 or obj_start < arr_start):
        if obj_end > obj_start:
            return text[obj_start : obj_end + 1]
    if arr_start != -1 and arr_end > arr_start:
        return text[arr_start : arr_end + 1]
    return text


class LLMRouter:
    """Async structured-output LLM client with automatic provider failover."""

    def __init__(
        self,
        gemini_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        openrouter_api_key: str | None = None,
        timeout_seconds: float = 30.0,
        apify_proxy_model: str | None = None,
        use_apify_proxy: bool | None = None,
    ) -> None:
        self.apify_token = os.getenv("APIFY_TOKEN")
        on_platform = os.getenv("APIFY_IS_AT_HOME") == "1"
        # The proxy only accepts the token of a real Actor run, so it is used on the platform only.
        self.use_apify_proxy = (on_platform if use_apify_proxy is None else use_apify_proxy) and bool(
            self.apify_token
        )
        self.apify_proxy_model = apify_proxy_model or DEFAULT_APIFY_PROXY_MODEL
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        self.anthropic_api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        self.openrouter_api_key = openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
        self.timeout_seconds = timeout_seconds

    @property
    def output_token_cap(self) -> int:
        """Usable completion-token budget of the first provider that will be tried."""
        return PROXY_OUTPUT_TOKEN_CAP if self.use_apify_proxy else DIRECT_OUTPUT_TOKEN_CAP

    def items_per_call(self, tokens_per_item: int, requested: int) -> int:
        """How many batch items fit in one reply without hitting the output cap."""
        overhead = PROXY_OUTPUT_OVERHEAD_TOKENS if self.use_apify_proxy else DIRECT_OUTPUT_OVERHEAD_TOKENS
        fit = (self.output_token_cap - overhead) // max(1, tokens_per_item)
        return max(1, min(requested, fit))

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.2,
    ) -> tuple[T, str]:
        """Generate a validated Pydantic object, failing over across providers.

        Returns:
            (parsed_model, provider_label)
        """
        errors: list[str] = []

        # --- Tier 0: Apify OpenRouter proxy (inside Apify runs) ---
        if self.use_apify_proxy:
            label = f"apify-openrouter:{self.apify_proxy_model}"
            try:
                result = await self._call_openai_compat(
                    base_url=APIFY_OPENROUTER_BASE_URL,
                    api_key=self.apify_token or "",
                    model_id=self.apify_proxy_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                    temperature=temperature,
                    # Claude models reject non-default sampling and may not accept json_object
                    # mode through the proxy; the schema contract in the prompt plus Pydantic
                    # validation enforce the structure instead.
                    json_mode=False,
                    send_temperature=not self.apify_proxy_model.startswith("anthropic/"),
                    max_tokens=PROXY_MAX_TOKENS,
                    min_timeout=CLAUDE_MIN_TIMEOUT_SECONDS,
                    extra_body={"reasoning": {"effort": PROXY_REASONING_EFFORT}},
                )
                logger.info("LLM call succeeded via %s", label)
                return result, label
            except Exception as exc:
                msg = f"{label} failed: {type(exc).__name__}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        # --- Tier 1 & 2: Google AI Studio (Gemini) ---
        if self.gemini_api_key:
            for model_id in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite"):
                label = f"gemini:{model_id}"
                try:
                    result = await self._call_gemini(
                        model_id=model_id,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        response_model=response_model,
                        temperature=temperature,
                    )
                    logger.info("LLM call succeeded via %s", label)
                    return result, label
                except Exception as exc:
                    msg = f"{label} failed: {type(exc).__name__}: {exc}"
                    logger.warning(msg)
                    errors.append(msg)

        # --- Tier 3: Anthropic Claude ---
        if self.anthropic_api_key:
            label = f"claude:{CLAUDE_MODEL}"
            try:
                result = await self._call_claude(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                )
                logger.info("LLM call succeeded via %s", label)
                return result, label
            except Exception as exc:
                msg = f"{label} failed: {type(exc).__name__}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        # --- Tier 4: OpenRouter Free Auto-Router ---
        if self.openrouter_api_key:
            label = "openrouter:openrouter/free"
            try:
                result = await self._call_openai_compat(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=self.openrouter_api_key,
                    model_id="openrouter/free",
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                    temperature=temperature,
                    extra_headers={
                        "HTTP-Referer": "https://apify.com",
                        "X-Title": "LeadBridge",
                    },
                )
                logger.info("LLM call succeeded via %s", label)
                return result, label
            except Exception as exc:
                msg = f"{label} failed: {type(exc).__name__}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        if not errors:
            raise LLMRouterError(
                "No LLM provider available. Run on the Apify platform (uses the Apify OpenRouter proxy) "
                "or set GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENROUTER_API_KEY."
            )
        raise LLMRouterError("All LLM providers failed:\n- " + "\n- ".join(errors))

    async def _call_gemini(
        self,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float,
    ) -> T:
        client = genai.Client(api_key=self.gemini_api_key)
        response = await client.aio.models.generate_content(
            model=model_id,
            contents=user_prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                response_mime_type="application/json",
                response_schema=response_model,
            ),
        )
        raw_text = response.text or ""
        if not raw_text.strip():
            raise ValueError("Gemini returned empty response text")
        cleaned = _extract_json_block(raw_text)
        return response_model.model_validate_json(cleaned)

    async def _call_claude(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        """Call Claude with native structured outputs (schema-constrained JSON).

        Note: Claude Opus 5.5 rejects sampling params (temperature etc.), so the
        router's ``temperature`` argument is intentionally not forwarded.
        """
        client = AsyncAnthropic(
            api_key=self.anthropic_api_key,
            timeout=max(self.timeout_seconds, CLAUDE_MIN_TIMEOUT_SECONDS),
        )
        request: dict = {
            "model": CLAUDE_MODEL,
            "max_tokens": 16000,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "output_format": response_model,
            "output_config": {"effort": CLAUDE_EFFORT},
        }
        if CLAUDE_MODEL in CLAUDE_FALLBACK_CAPABLE:
            # On a safety-classifier refusal, the API re-runs the request on a
            # fallback model inside the same call.
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"
        response = await client.beta.messages.parse(**request)
        if response.stop_reason == "refusal":
            raise ValueError(f"{CLAUDE_MODEL} refused the request: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise ValueError(f"{CLAUDE_MODEL} hit max_tokens before finishing the JSON output")
        parsed = response.parsed_output
        if parsed is None:
            raise ValueError(f"{CLAUDE_MODEL} returned no parsable structured output")
        return parsed

    async def _call_openai_compat(
        self,
        base_url: str,
        api_key: str,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float,
        extra_headers: dict[str, str] | None = None,
        json_mode: bool = True,
        send_temperature: bool = True,
        max_tokens: int | None = None,
        min_timeout: float = 0.0,
        extra_body: dict | None = None,
    ) -> T:
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        enforced_system = (
            f"{system_prompt}\n\n"
            "CRITICAL OUTPUT CONTRACT: Return ONLY a valid JSON object matching this exact JSON Schema. "
            "No markdown fences, no commentary.\n"
            f"JSON Schema:\n{schema_json}"
        )

        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=max(self.timeout_seconds, min_timeout),
            default_headers=extra_headers or {},
        )
        request: dict = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": enforced_system},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        if send_temperature:
            request["temperature"] = temperature
        if max_tokens:
            request["max_tokens"] = max_tokens
        if extra_body:
            request["extra_body"] = extra_body
        completion = await client.chat.completions.create(**request)
        choice = completion.choices[0]
        raw_text = choice.message.content or ""
        finish = getattr(choice, "finish_reason", None)
        native_finish = getattr(choice, "native_finish_reason", None) or (
            (getattr(choice, "model_extra", None) or {}).get("native_finish_reason")
        )
        if not raw_text.strip():
            raise ValueError(
                f"{model_id} returned empty content (finish_reason={finish}, native={native_finish})"
            )
        cleaned = _extract_json_block(raw_text)
        try:
            return response_model.model_validate_json(cleaned)
        except Exception as exc:
            # Say WHY the output is unusable: a length cut, a provider content filter / refusal,
            # or genuinely malformed JSON. Without this the log only shows a bare JSON error.
            raise ValueError(
                f"{model_id} output failed validation (finish_reason={finish}, native={native_finish}, "
                f"chars={len(raw_text)}, usage={getattr(completion, 'usage', None)}, tail={raw_text[-120:]!r}): "
                f"{type(exc).__name__}: {str(exc)[:200]}"
            ) from exc