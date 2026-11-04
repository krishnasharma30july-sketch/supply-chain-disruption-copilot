"""The self-hosted last resort (Qwen3-4B on vLLM): chain position, request shape, schema fallback, unreachable server."""

import asyncio
import json

import httpx
import pytest

from app.core.config import get_settings
from app.llm.outputs import AdjudicationBatchOut
from app.llm.service import LLMCallError, LLMService

OK_JSON = json.dumps({"decisions": [{"case_id": "AB12CD34", "choice": "APPLY_RULE_PLAN", "rationale": "Rule plan fits."}]})


def settings(**overrides):
    base = {"llm_provider": "gemini", "gemini_api_key": None, "groq_api_key": None,
            "local_llm_enabled": True, "llm_max_retries": 2, "groq_rpm": 600, "local_llm_rpm": 600, **overrides}
    return get_settings().model_copy(update=base)


def completion(content: str, finish: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 60},
    })


def local_service(handler, **overrides) -> LLMService:
    client = httpx.AsyncClient(base_url="http://vllm.test/v1", transport=httpx.MockTransport(handler))
    return LLMService(settings(**overrides), session_factory=None, http_clients={"local": client})


def ask(llm: LLMService, **kw):
    return asyncio.run(llm.generate(
        agent="mitigation", role="adjudicator", prompt="adjudicator", context={"policy": "p", "cases_json": "[]"},
        schema=AdjudicationBatchOut, use_cache=False, **kw,
    ))


def test_local_model_is_always_the_last_link_of_the_chain_and_off_by_default():
    assert ("local", "qwen3-4b") not in get_settings().model_copy(update={"local_llm_enabled": False}).llm_chain("email")
    full = settings(gemini_api_key="g", groq_api_key="q")
    chain = full.llm_chain("email")
    assert chain[-1] == ("local", "qwen3-4b") and [p for p, _ in chain].count("local") == 1
    assert [p for p, _ in chain][0] == "gemini"
    groq_first = full.model_copy(update={"llm_provider": "groq"}).llm_chain("email")
    assert groq_first[0][0] == "groq" and groq_first[-1] == ("local", "qwen3-4b")


def test_local_model_alone_enables_the_llm_without_any_api_key():
    s = settings()
    assert s.llm_enabled and s.llm_chain("email") == [("local", "qwen3-4b")]
    assert not settings(llm_provider="offline").llm_enabled


def test_request_enforces_the_schema_switches_thinking_off_and_fits_the_context():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return completion(OK_JSON)

    res = ask(local_service(handler), max_output_tokens=8192)
    assert res.parsed.decisions[0].case_id == "AB12CD34" and res.model == "qwen3-4b"
    body = seen["body"]
    assert body["model"] == "qwen3-4b"
    assert body["response_format"]["type"] == "json_schema"
    assert "APPLY_RULE_PLAN" in json.dumps(body["response_format"]["json_schema"]["schema"])
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_tokens"] == get_settings().local_llm_max_output_tokens  # prompt + reply must fit the 8k context
    assert "JSON Schema" in body["messages"][0]["content"]  # the schema is also in the prompt


def test_a_schema_the_grammar_backend_rejects_is_retried_in_plain_json_mode():
    formats = []

    def handler(request: httpx.Request) -> httpx.Response:
        formats.append(json.loads(request.content)["response_format"]["type"])
        if len(formats) == 1:
            return httpx.Response(400, text='{"error":"The provided JSON schema contains features not supported by xgrammar"}')
        return completion(OK_JSON)

    assert ask(local_service(handler)).parsed.decisions
    assert formats == ["json_schema", "json_object"]


def test_a_context_overflow_is_a_plain_error_not_a_schema_retry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text='{"error":"maximum context length is 8192 tokens, your request has 9100"}')

    with pytest.raises(LLMCallError):
        ask(local_service(handler))
    assert len(calls) == 1


def test_a_server_that_is_not_running_is_skipped_at_once_without_retries():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMCallError, match="not reachable"):
        ask(local_service(handler, llm_max_retries=3))
    assert len(calls) == 1  # no 4s/8s/16s back-off for a container that is simply off


def test_when_groq_fails_the_local_model_answers():
    groq = httpx.AsyncClient(base_url="https://groq.test/v1", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    local = httpx.AsyncClient(base_url="http://vllm.test/v1", transport=httpx.MockTransport(lambda r: completion(OK_JSON)))
    llm = LLMService(settings(llm_provider="groq", groq_api_key="q", llm_max_retries=0), session_factory=None,
                     http_clients={"groq": groq, "local": local})
    res = ask(llm)
    assert res.model == "qwen3-4b" and res.parsed.decisions


def test_the_local_model_gets_a_longer_timeout_than_the_apis():
    llm = local_service(lambda r: completion(OK_JSON))
    assert llm._timeout("local") == get_settings().local_llm_timeout_s > llm._timeout("groq")
