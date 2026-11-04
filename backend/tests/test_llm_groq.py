"""Groq as a second LLM provider: model chain order, JSON-mode requests, quota and key handling (HTTP is mocked)."""

import asyncio
import json

import httpx
import pytest

from app.core.config import get_settings
from app.llm.outputs import AdjudicationBatchOut
from app.llm.service import LLMCallError, LLMService, LLMUnavailable, _groq_wait_seconds

FIRST, SECOND = get_settings().groq_models[:2]
OK_JSON = json.dumps({"decisions": [{"case_id": "AB12CD34", "choice": "APPLY_RULE_PLAN", "rationale": "Rule plan fits."}]})


def settings(**overrides):
    base = {"llm_provider": "groq", "gemini_api_key": None, "groq_api_key": "test-groq-key",
            "llm_max_retries": 1, "groq_rpm": 600, **overrides}
    return get_settings().model_copy(update=base)


def service(handler, **overrides) -> LLMService:
    client = httpx.AsyncClient(base_url="https://groq.test/openai/v1", transport=httpx.MockTransport(handler))
    return LLMService(settings(**overrides), session_factory=None, http_client=client)


def ask(llm: LLMService, **kw):
    return asyncio.run(llm.generate(
        agent="mitigation", role="adjudicator", prompt="adjudicator", context={"policy": "p", "cases_json": "[]"},
        schema=AdjudicationBatchOut, use_cache=False, **kw,
    ))


def completion(content: str, finish: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    })


def test_chain_puts_the_configured_primary_first_and_skips_providers_without_a_key():
    both = get_settings().model_copy(update={"llm_provider": "gemini", "gemini_api_key": "g", "groq_api_key": "q"})
    chain = both.llm_chain("email")
    assert chain[0][0] == "gemini" and chain[-1][0] == "groq"
    assert [p for p, _ in chain].count("gemini") == 1 + len(both.llm_fallback_models)

    groq_first = both.model_copy(update={"llm_provider": "groq"}).llm_chain("email")
    assert groq_first[0][0] == "groq" and groq_first[-1][0] == "gemini"

    only_gemini = both.model_copy(update={"groq_api_key": None}).llm_chain("email")
    assert {p for p, _ in only_gemini} == {"gemini"}
    assert not get_settings().model_copy(update={"gemini_api_key": None, "groq_api_key": None}).llm_enabled
    assert get_settings().model_copy(update={"llm_provider": "groq", "groq_api_key": "q"}).llm_enabled
    assert not get_settings().model_copy(update={"llm_provider": "offline", "groq_api_key": "q"}).llm_enabled


def test_groq_request_uses_json_mode_and_carries_the_schema_in_the_prompt():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return completion(OK_JSON)

    res = ask(service(handler))
    assert res.parsed.decisions[0].choice == "APPLY_RULE_PLAN"
    assert res.model == get_settings().groq_models[0] and not res.cached
    assert seen["path"].endswith("/chat/completions")
    body = seen["body"]
    assert body["response_format"] == {"type": "json_object"}
    system = body["messages"][0]["content"]
    assert "JSON Schema" in system and "APPLY_RULE_PLAN" in system  # the schema (with its enum) travels in the prompt
    assert body["max_tokens"] <= 8192


def test_markdown_fences_around_the_json_are_tolerated():
    res = ask(service(lambda r: completion("```json\n" + OK_JSON + "\n```")))
    assert res.parsed.decisions[0].case_id == "AB12CD34"


def test_a_daily_quota_error_moves_to_the_next_model_and_remembers_the_first_is_spent():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == FIRST:
            return httpx.Response(429, json={"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 100000, "
                                                                  "Used 99900. Please try again in 17m12.5s."}})
        return completion(OK_JSON)

    llm = service(handler)
    assert ask(llm).model == SECOND
    assert llm.exhausted_models() == [FIRST]
    calls.clear()
    ask(llm)
    assert calls == [SECOND]  # the spent model is not asked again


def test_when_every_model_is_out_of_quota_the_gateway_reports_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "requests per day (RPD) reached. Please try again in 2h3m."}})

    llm = service(handler)
    with pytest.raises(LLMUnavailable):
        ask(llm)
    assert not llm.available


def test_a_rejected_key_disables_the_provider_instead_of_retrying_forever():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

    llm = service(handler)
    with pytest.raises((LLMCallError, LLMUnavailable)):
        ask(llm)
    assert "groq" in llm.disabled_providers() and "401" in llm.disabled_providers()["groq"]
    n = len(calls)
    with pytest.raises(LLMUnavailable, match="No usable LLM provider"):
        ask(llm)
    assert len(calls) == n  # no further requests once the key is known to be bad


def test_invalid_json_is_retried_then_accepted():
    answers = iter([completion("not json at all"), completion(OK_JSON)])
    res = ask(service(lambda r: next(answers), llm_max_retries=2))
    assert res.parsed.decisions


def test_json_validate_failed_from_groq_counts_as_a_retryable_output_error():
    answers = iter([httpx.Response(400, json={"error": {"code": "json_validate_failed", "message": "bad"}}), completion(OK_JSON)])
    assert ask(service(lambda r: next(answers), llm_max_retries=2)).parsed.decisions


def test_truncated_generation_is_rejected():
    with pytest.raises(LLMCallError):
        ask(service(lambda r: completion(OK_JSON, finish="length"), llm_max_retries=0))


def test_gemini_failure_falls_through_to_groq():
    """Gemini first; when its models fail, Groq answers."""
    import asyncio as _asyncio

    from app.llm.service import _ApiError

    s = get_settings().model_copy(update={"llm_provider": "gemini", "gemini_api_key": "g", "groq_api_key": "q",
                                         "llm_max_retries": 0, "llm_rpm": 600, "groq_rpm": 600})
    client = httpx.AsyncClient(base_url="https://groq.test/v1", transport=httpx.MockTransport(lambda r: completion(OK_JSON)))
    llm = LLMService(s, session_factory=None, http_clients={"groq": client})

    async def failing_gemini(model, system, user, gschema, max_output_tokens):
        raise _ApiError(500, "backend error")

    llm._request_gemini = failing_gemini  # type: ignore[method-assign]
    res = _asyncio.run(llm.generate(agent="mitigation", role="adjudicator", prompt="adjudicator",
                                    context={"policy": "p", "cases_json": "[]"}, schema=AdjudicationBatchOut, use_cache=False))
    assert res.model == FIRST and res.parsed.decisions


def test_wait_time_parsing():
    assert _groq_wait_seconds("Please try again in 17m12.5s.", None) == pytest.approx(17 * 60 + 12.5)
    assert _groq_wait_seconds("try again in 2h3m", None) == 2 * 3600 + 3 * 60
    assert _groq_wait_seconds("try again in 250ms", None) == pytest.approx(0.25)
    assert _groq_wait_seconds("no hint", "42") == 42.0
    assert _groq_wait_seconds("no hint", None) is None
