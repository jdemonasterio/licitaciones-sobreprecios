import pytest

from sobreprecios import llm


class FakeError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status_code = status


@pytest.mark.parametrize("msg,status,expected", [
    ("429 RESOURCE_EXHAUSTED: rate limit", 429, "retry"),
    ("Quota exceeded for metric generate_content_free_tier_requests, limit PerDay", 429, "quota"),
    ("You exceeded your current quota, check your plan and billing", 429, "quota"),
    ("API key not valid", 400, "fatal"),
    ("upstream timeout", 503, "retry"),
])
def test_classify_error(msg, status, expected):
    assert llm.classify_error(FakeError(msg, status)) == expected


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("LIC_LLM_OFFLINE", raising=False)
    monkeypatch.setenv("LIC_LLM_PERFIL", "pago")
    monkeypatch.setenv("LIC_LLM_ROLE_CHEAP", "gpt-6-luna")
    llm.reset_budget()
    return tmp_path


def test_complete_json_caches_and_counts_cost(isolated, monkeypatch):
    calls = []

    def fake_openai(model, prompt, schema, name, effort="low"):
        calls.append(model)
        return '{"ok": true}', 1000, 100

    monkeypatch.setattr(llm, "_call_openai", fake_openai)
    assert llm.complete_json("hola", {"type": "object"}) == {"ok": True}
    assert llm.complete_json("hola", {"type": "object"}) == {"ok": True}   # cache
    assert calls == ["gpt-6-luna"]
    assert llm.run_spend() == pytest.approx((1000 * 0.10 + 100 * 0.50) / 1e6)


def test_complete_json_offline_raises(isolated, monkeypatch):
    monkeypatch.setenv("LIC_LLM_OFFLINE", "1")
    with pytest.raises(llm.LLMUnavailable):
        llm.complete_json("hola", {"type": "object"})


def test_complete_json_quota_is_not_retried(isolated, monkeypatch):
    calls = []

    def quota(model, prompt, schema, name, effort="low"):
        calls.append(1)
        raise FakeError("limit PerDay exceeded", 429)

    monkeypatch.setattr(llm, "_call_openai", quota)
    with pytest.raises(llm.QuotaExhausted):
        llm.complete_json("hola", {"type": "object"})
    assert calls == [1]


def test_free_profile_costs_nothing(isolated, monkeypatch):
    monkeypatch.setenv("LIC_LLM_PERFIL", "gratis")
    assert llm.estimate_cost("gemini-flash-lite-latest", 10_000, 10_000) == 0.0
