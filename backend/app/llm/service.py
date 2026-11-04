"""LLM gateway: structured output from Gemini and, as fallbacks, Groq and a self-hosted vLLM model (OpenAI-compatible chat APIs), with a
model fallback chain, caching, rate limiting, retries, a circuit breaker and accounting.

Callers always receive a schema-validated Pydantic object or an exception; they own the fallback path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import record_llm_call
from app.db.models import LlmCache, LlmCall
from app.llm.schema import to_gemini_schema

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

PROMPT_VERSIONS = {
    "intake": "intake-v1",
    "email": "email-v3",
    "adjudicator": "adjudicator-v1",
    "investigator": "investigator-v1",
    "narrative": "narrative-v2",
    "tone": "tone-v1",
    "call_brief": "call-brief-v1",
    "seller_notice": "seller-notice-v1",
}


class LLMUnavailable(RuntimeError):
    """The LLM is disabled, unconfigured, or the circuit breaker is open."""


class LLMCallError(RuntimeError):
    pass


class _NoRetry(LLMCallError):
    """A failure that retrying cannot fix (the self-hosted server is not running): move on to the next model."""


class _QuotaExhausted(Exception):
    """A model's daily quota is used up; the gateway moves on to the next model in the chain."""

    def __init__(self, retry_in_s: float):
        super().__init__(f"quota exhausted, retry in {retry_in_s:.0f}s")
        self.retry_in_s = retry_in_s


class _ApiError(Exception):
    """A provider-neutral HTTP-style failure raised by the per-provider request functions."""

    def __init__(self, code: int | None, message: str, retry_after: float | None = None, daily_quota: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.daily_quota = daily_quota


@dataclass
class LLMResult(Generic[T]):
    parsed: T
    model: str
    prompt_version: str
    cached: bool
    latency_ms: int


class RateLimiter:
    """Sliding-window requests-per-minute limiter (per process)."""

    def __init__(self, rpm: int):
        self.rpm = rpm
        self.calls: deque[float] = deque()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.calls and now - self.calls[0] >= 60:
                    self.calls.popleft()
                if len(self.calls) < self.rpm:
                    self.calls.append(now)
                    return
                await asyncio.sleep(60 - (now - self.calls[0]) + 0.05)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t


def _retry_delay(err: Exception) -> float | None:
    """Seconds until retry, from Gemini's RetryInfo ('retryDelay': '62861s') when present."""
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", str(err), re.I)
    return float(m.group(1)) if m else None


_GROQ_WAIT_RE = re.compile(r"try again in\s+((?:\d+(?:\.\d+)?[hms]+\s*)+)", re.I)
_GROQ_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)", re.I)
_SCHEMA_REJECTED_RE = re.compile(r"schema|grammar|guided|xgrammar|structured output", re.I)
_GROQ_DAILY_RE = re.compile(r"per day|\(TPD\)|\(RPD\)", re.I)


def _groq_wait_seconds(body: str, header: str | None) -> float | None:
    """Seconds to wait, from the Retry-After header or the 'Please try again in 1m26.4s' text of a 429."""
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    m = _GROQ_WAIT_RE.search(body)
    if not m:
        return None
    unit = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(n) * unit[u.lower()] for n, u in _GROQ_PART_RE.findall(m.group(1))) or None


class LLMService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] | None,
        http_client: httpx.AsyncClient | None = None,
        http_clients: dict[str, httpx.AsyncClient] | None = None,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.enabled = settings.llm_enabled
        self._client = None  # Gemini
        # OpenAI-compatible chat APIs (Groq, local vLLM), one HTTP client each. Tests inject their own.
        self._http: dict[str, httpx.AsyncClient] = dict(http_clients or {})
        if http_client is not None:
            self._http.setdefault("groq", http_client)
        self._disabled: dict[str, str] = {}  # provider -> why it is unusable for the rest of the process (bad key)
        if self.enabled and settings.gemini_api_key:
            from google import genai

            self._client = genai.Client(api_key=settings.gemini_api_key)
        if self.enabled:
            for provider, key, base in (
                ("groq", settings.groq_api_key, settings.groq_base_url),
                ("local", settings.local_llm_api_key if settings.local_llm_enabled else None, settings.local_llm_base_url),
            ):
                if key and provider not in self._http:
                    self._http[provider] = httpx.AsyncClient(
                        base_url=base.rstrip("/"), headers={"Authorization": f"Bearer {key}"},
                        timeout=httpx.Timeout(self._timeout(provider), connect=5.0),
                    )
        self._sem = asyncio.Semaphore(settings.llm_max_concurrency)
        self._rpm = {
            "gemini": RateLimiter(settings.llm_rpm), "groq": RateLimiter(settings.groq_rpm),
            "local": RateLimiter(settings.local_llm_rpm),
        }
        self._failures = 0
        self._breaker_open = False
        self._breaker_until = 0.0
        self.breaker_reason: str | None = None
        self._exhausted: dict[str, float] = {}  # model -> monotonic time its daily quota resets (process-wide)
        self._env = Environment(
            loader=FileSystemLoader(str(settings.prompts_dir)), undefined=StrictUndefined, autoescape=False,
            keep_trailing_newline=True,
        )

    def _timeout(self, provider: str) -> float:
        return self.settings.local_llm_timeout_s if provider == "local" else self.settings.llm_request_timeout_s

    # ------------------------------------------------------------------ state

    @property
    def available(self) -> bool:
        return self.enabled and not self._breaker_is_open()

    @property
    def mode(self) -> str:
        return "online" if self.enabled else "offline"

    def reset_breaker(self) -> None:
        self._failures = 0
        self._breaker_open = False
        self.breaker_reason = None

    def _breaker_is_open(self) -> bool:
        """Open breakers close again after their cooldown (half-open: the next call is a trial)."""
        if self._breaker_open and time.monotonic() >= self._breaker_until:
            log.info("llm_breaker_half_open", previous_reason=self.breaker_reason)
            self.reset_breaker()
        return self._breaker_open

    def _trip(self, reason: str, cooldown_s: float | None = None) -> None:
        self._breaker_open = True
        self.breaker_reason = reason
        self._breaker_until = time.monotonic() + (cooldown_s if cooldown_s is not None else self.settings.llm_breaker_cooldown_s)
        log.warning("llm_breaker_open", reason=reason, cooldown_s=round(self._breaker_until - time.monotonic()))

    def _trip_quota(self) -> None:
        now = time.monotonic()
        resets = [until - now for until in self._exhausted.values() if until > now]
        self._trip("daily quota exhausted on every configured model", min(resets) if resets else None)

    def render(self, prompt: str, **ctx: Any) -> tuple[str, str]:
        system = self._env.get_template(f"{prompt}.system.j2").render(**ctx)
        user = self._env.get_template(f"{prompt}.user.j2").render(**ctx)
        return system, user

    # ------------------------------------------------------------------ main entry

    async def generate(
        self,
        *,
        agent: str,
        role: str,
        prompt: str,
        context: dict[str, Any],
        schema: type[T],
        run_id: str | None = None,
        max_output_tokens: int = 8192,
        use_cache: bool = True,
    ) -> LLMResult[T]:
        """use_cache=False forces a fresh generation (an explicit "regenerate" must not replay the cached answer)."""
        if not self.enabled:
            raise LLMUnavailable("LLM is not configured (offline mode).")
        if self._breaker_is_open():
            raise LLMUnavailable(f"LLM circuit breaker is open: {self.breaker_reason}")
        version = PROMPT_VERSIONS[prompt]
        system, user = self.render(prompt, **context)

        # Model chain: the primary provider's models first, then the other provider's (each model has its own quota).
        chain = [(p, m) for p, m in self.settings.llm_chain(role) if p not in self._disabled]
        if not chain:
            reasons = "; ".join(f"{p}: {r}" for p, r in self._disabled.items()) or "no API key configured"
            raise LLMUnavailable(f"No usable LLM provider ({reasons}).")
        available = [(p, m) for p, m in chain if not self._is_exhausted(m)]
        if not available:
            self._trip_quota()
            raise LLMUnavailable("LLM daily quota exhausted on every configured model")

        errors: list[str] = []
        for provider, model in available:
            if provider in self._disabled:  # a bad key found by an earlier model of the same provider in this loop
                continue
            try:
                return await self._generate_with(
                    provider, model, agent, version, system, user, schema, run_id, max_output_tokens, use_cache
                )
            except _QuotaExhausted as e:
                errors.append(f"{model}: daily quota exhausted")
                log.warning("llm_model_quota_exhausted", provider=provider, model=model, retry_in_s=e.retry_in_s)
            except LLMCallError as e:
                errors.append(f"{model}: {e}")
                log.warning("llm_model_failed_trying_next", provider=provider, model=model, error=str(e)[:200])
        if all(self._is_exhausted(m) for _, m in chain):
            self._trip_quota()
            raise LLMUnavailable("LLM daily quota exhausted on every configured model")
        self._register_failure(errors[-1] if errors else "unknown error")
        raise LLMCallError("; ".join(errors)[:1500])

    def _is_exhausted(self, model: str) -> bool:
        until = self._exhausted.get(model)
        return until is not None and time.monotonic() < until

    def exhausted_models(self) -> list[str]:
        return [m for m in self._exhausted if self._is_exhausted(m)]

    def disabled_providers(self) -> dict[str, str]:
        return dict(self._disabled)

    async def _request_gemini(
        self, model: str, system: str, user: str, gschema: dict, max_output_tokens: int
    ) -> tuple[str, str | None, int | None, int | None]:
        """One Gemini call. Returns (text, finish, input_tokens, output_tokens); API failures become _ApiError."""
        from google.genai import errors as genai_errors
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=gschema,
            temperature=self.settings.llm_temperature,
            max_output_tokens=max_output_tokens,
        )
        try:
            resp = await self._client.aio.models.generate_content(model=model, contents=user, config=config)  # type: ignore[union-attr]
        except genai_errors.APIError as e:
            raise _ApiError(
                getattr(e, "code", None), str(e)[:1500], _retry_delay(e), bool(re.search(r"PerDay|per day", str(e), re.I))
            ) from e
        finish = None
        if resp.candidates:
            fr = resp.candidates[0].finish_reason
            finish = getattr(fr, "name", None) or (str(fr) if fr is not None else None)
        usage = resp.usage_metadata
        in_tok = getattr(usage, "prompt_token_count", None) if usage else None
        out_tok = getattr(usage, "candidates_token_count", None) if usage else None
        return resp.text or "", finish, in_tok, out_tok

    async def _request_openai(
        self, provider: str, model: str, system: str, user: str, schema: type[BaseModel], max_output_tokens: int
    ) -> tuple[str, str | None, int | None, int | None]:
        """One call to an OpenAI-compatible chat API (Groq, local vLLM) in JSON mode. The schema travels in the system prompt
        and the reply is validated with Pydantic afterwards, because JSON mode does not enforce a schema (the local vLLM
        server additionally enforces it while decoding)."""
        http = self._http[provider]
        local = provider == "local"
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": (
                    f"{system}\n\nRespond with ONE JSON object that validates against this JSON Schema. "
                    f"Output JSON only: no prose and no markdown fences.\nJSON Schema: {schema_json}")},
                {"role": "user", "content": user},
            ],
            "temperature": self.settings.llm_temperature,
            "max_tokens": min(max_output_tokens, self.settings.local_llm_max_output_tokens if local else 16384),
            "response_format": {"type": "json_object"},
        }
        if local:
            # vLLM enforces the schema while decoding (a 4B model must not be trusted to follow it from the prompt alone)
            # and Qwen3's thinking mode is switched off: its reasoning tokens would eat the small output budget.
            payload["response_format"] = {
                "type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if provider == "groq" and model.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = "low"  # these models think first; keep that short so the reply fits the budget
        try:
            resp = await http.post("/chat/completions", json=payload)
            if local and resp.status_code == 400 and _SCHEMA_REJECTED_RE.search(resp.text):
                payload["response_format"] = {"type": "json_object"}  # a schema feature the grammar backend lacks
                resp = await http.post("/chat/completions", json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            if local:  # the container is not running or still loading the model: skip it, do not retry
                raise _NoRetry(f"local model server not reachable at {self.settings.local_llm_base_url}") from e
            raise
        if resp.status_code >= 400:
            body = resp.text[:1500]
            if resp.status_code == 400 and "json_validate_failed" in body:
                raise LLMCallError(f"invalid structured output from {model}: {body[:300]}")  # worth another attempt
            raise _ApiError(
                resp.status_code, body, _groq_wait_seconds(body, resp.headers.get("retry-after")),
                resp.status_code == 429 and bool(_GROQ_DAILY_RE.search(body)),
            )
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason")
        usage = data.get("usage") or {}
        return (
            (choice.get("message") or {}).get("content") or "",
            "STOP" if finish == "stop" else (str(finish).upper() if finish else None),
            usage.get("prompt_tokens"), usage.get("completion_tokens"),
        )

    async def _request(
        self, provider: str, model: str, system: str, user: str, wire_schema: dict, schema: type[BaseModel], max_output_tokens: int
    ) -> tuple[str, str | None, int | None, int | None]:
        if provider in self._http:
            return await self._request_openai(provider, model, system, user, schema, max_output_tokens)
        return await self._request_gemini(model, system, user, wire_schema, max_output_tokens)

    async def _generate_with(
        self,
        provider: str,
        model: str,
        agent: str,
        version: str,
        system: str,
        user: str,
        schema: type[T],
        run_id: str | None,
        max_output_tokens: int,
        use_cache: bool = True,
    ) -> LLMResult[T]:
        wire_schema = to_gemini_schema(schema) if provider == "gemini" else schema.model_json_schema()
        request_hash = hashlib.sha256(
            json.dumps({"m": model, "v": version, "s": system, "u": user, "j": wire_schema}, sort_keys=True).encode()
        ).hexdigest()
        cached = await self._cache_get(request_hash) if use_cache else None
        if cached is not None:
            try:
                parsed = schema.model_validate_json(cached)
                await self._account(run_id, agent, model, version, request_hash, True, "ok", "CACHE", None, None, 0, 0, None)
                return LLMResult(parsed=parsed, model=model, prompt_version=version, cached=True, latency_ms=0)
            except ValidationError:
                pass  # schema changed since caching; call the model again

        last_error = "unknown error"
        attempts = 0
        async with self._sem:
            for attempt in range(1, self.settings.llm_max_retries + 2):
                attempts = attempt
                if self._breaker_open:
                    raise LLMUnavailable(f"LLM circuit breaker is open: {self.breaker_reason}")
                await self._rpm[provider].acquire()
                t0 = time.monotonic()
                retry_after: float | None = None
                head = ""  # the start of the model's reply, kept for the error message when it cannot be parsed
                try:
                    text, finish, in_tok, out_tok = await asyncio.wait_for(
                        self._request(provider, model, system, user, wire_schema, schema, max_output_tokens),
                        timeout=self._timeout(provider),
                    )
                    head = text[:160]
                    latency = int((time.monotonic() - t0) * 1000)
                    if finish not in (None, "STOP"):
                        raise LLMCallError(f"generation stopped early (finish_reason={finish})")
                    if not text.strip():
                        raise LLMCallError("empty response")
                    clean = _strip_fences(text)
                    parsed = schema.model_validate_json(clean)
                    await self._cache_put(request_hash, model, clean)
                    await self._account(run_id, agent, model, version, request_hash, False, "ok", finish, in_tok, out_tok, latency, attempts, None)
                    self._failures = 0
                    return LLMResult(parsed=parsed, model=model, prompt_version=version, cached=False, latency_ms=latency)
                except asyncio.TimeoutError:
                    last_error = f"timeout after {self._timeout(provider):g}s"
                except (ValidationError, json.JSONDecodeError) as e:
                    last_error = f"invalid structured output: {str(e)[:400]} | reply starts: {head!r}"
                except _NoRetry as e:
                    await self._account(run_id, agent, model, version, request_hash, False, "error", None, None, None, 0, attempts, str(e))
                    raise
                except LLMCallError as e:
                    last_error = str(e)
                except _ApiError as e:
                    code = e.code
                    last_error = f"{provider} API error {code}: {e.message}"
                    if code == 429:
                        if e.daily_quota:
                            retry_in = e.retry_after or 3600.0
                            self._exhausted[model] = time.monotonic() + retry_in
                            await self._account(run_id, agent, model, version, request_hash, False, "quota", None, None, None, int((time.monotonic() - t0) * 1000), attempts, last_error)
                            raise _QuotaExhausted(retry_in) from e
                        retry_after = e.retry_after
                    elif code in (401, 403):
                        # A rejected key will not start working: stop using this provider for the rest of the process.
                        self._disabled[provider] = f"authentication failed (HTTP {code})"
                        log.warning("llm_provider_disabled", provider=provider, reason=self._disabled[provider])
                        await self._account(run_id, agent, model, version, request_hash, False, "error", None, None, None, int((time.monotonic() - t0) * 1000), attempts, last_error)
                        raise LLMCallError(last_error) from e
                    elif code is not None and 400 <= code < 500 and code not in (408, 409):
                        await self._account(run_id, agent, model, version, request_hash, False, "error", None, None, None, int((time.monotonic() - t0) * 1000), attempts, last_error)
                        raise LLMCallError(last_error) from e
                except Exception as e:  # network errors and other transient failures
                    last_error = f"{type(e).__name__}: {str(e)[:500]}"
                log.warning("llm_attempt_failed", agent=agent, model=model, attempt=attempt, error=last_error[:300])
                if attempt <= self.settings.llm_max_retries:
                    # 4s, 8s, 16s (+ jitter): Gemini "high demand" 503 spikes usually clear within ~30s.
                    delay = retry_after if retry_after is not None else min(30.0, 4.0 * (2 ** (attempt - 1)))
                    await asyncio.sleep(min(60.0, delay) + random.uniform(0, 0.5))

        await self._account(run_id, agent, model, version, request_hash, False, "error", None, None, None, 0, attempts, last_error)
        raise LLMCallError(last_error)

    def _register_failure(self, reason: str) -> None:
        self._failures += 1
        if self._failures >= self.settings.llm_breaker_threshold:
            self._trip(f"{self._failures} consecutive failures (last: {reason[:120]})")

    # ------------------------------------------------------------------ persistence

    async def _cache_get(self, request_hash: str) -> str | None:
        if self.session_factory is None:
            return None
        async with self.session_factory() as s:
            row = (await s.execute(select(LlmCache.response_text).where(LlmCache.request_hash == request_hash))).scalar()
            return row

    async def _cache_put(self, request_hash: str, model: str, text: str) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as s:
            await s.execute(
                pg_insert(LlmCache)
                .values(request_hash=request_hash, model=model, response_text=text)
                .on_conflict_do_nothing(index_elements=["request_hash"])
            )
            await s.commit()

    async def _account(
        self, run_id, agent, model, version, request_hash, cached, status, finish, in_tok, out_tok, latency, attempts, error
    ) -> None:
        record_llm_call(agent, model, status, cached, in_tok, out_tok, latency)
        if self.session_factory is None:
            return
        try:
            async with self.session_factory() as s:
                s.add(
                    LlmCall(
                        run_id=run_id, agent=agent, model=model, prompt_version=version, request_hash=request_hash,
                        cached=cached, status=status, finish_reason=finish, input_tokens=in_tok, output_tokens=out_tok,
                        latency_ms=latency, attempts=attempts, error=error,
                    )
                )
                await s.commit()
        except Exception:  # accounting must never break a run
            log.exception("llm_accounting_failed")
