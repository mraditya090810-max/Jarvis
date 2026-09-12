"""
Offline tests for core.ai — no network access, no OpenRouter key required.
Covers: prompt optimization, model ranking, cache round-trip, and the
fallback chain against fake providers (success-on-Nth-model, all-fail).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ai import prompt_optimizer, model_selector, cache
from core.ai.exceptions import NoFreeModelAvailable, ProviderError, RateLimitedError
from core.ai.retry import run_with_fallback


def test_dedupe_imports():
    text = "import os\nimport sys\nimport os\nfrom pathlib import Path\n"
    out = prompt_optimizer.dedupe_imports(text)
    assert out.count("import os") == 1
    assert "from pathlib import Path" in out


def test_collapse_blank_lines():
    text = "a\n\n\n\nb"
    out = prompt_optimizer.collapse_blank_lines(text)
    assert "\n\n\n" not in out


def test_truncate_to_token_budget():
    text = "x" * 10_000
    out = prompt_optimizer.truncate_to_token_budget(text, max_tokens=10)
    assert len(out) <= 10 * 4 + len("\n...[truncated — context exceeded token budget]")


def test_dedupe_context_blocks():
    blocks = [("a", "same content"), ("b", "same content"), ("c", "different")]
    out = prompt_optimizer.dedupe_context_blocks(blocks)
    assert len(out) == 2


def test_model_ranking_prefers_coding_family_and_context():
    models = [
        {"id": "vendor/tiny-general:free", "context_length": 4000},
        {"id": "vendor/deepseek-coder:free", "context_length": 32000},
        {"id": "vendor/unknown-family:free", "context_length": 128000},
    ]
    ranked = model_selector.rank_models(models)
    # The coding-family model should rank at or above the unrecognized one,
    # and the tiny/general model should rank last.
    assert ranked[-1] == "vendor/tiny-general:free"
    assert "vendor/deepseek-coder:free" in ranked[:2]


def test_vision_model_ranking_filters_to_image_capable_only():
    models = [
        {"id": "vendor/text-only-coder:free", "context_length": 32000},
        {"id": "vendor/some-vl-model:free", "context_length": 32000},
        {"id": "vendor/vision-preview:free", "context_length": 16000,
         "architecture": {"input_modalities": ["text", "image"]}},
    ]
    ranked = model_selector.rank_vision_models(models)
    assert "vendor/text-only-coder:free" not in ranked
    assert "vendor/some-vl-model:free" in ranked
    assert "vendor/vision-preview:free" in ranked


def test_vision_model_ranking_empty_when_no_free_vision_model():
    models = [{"id": "vendor/text-only-coder:free", "context_length": 32000}]
    assert model_selector.rank_vision_models(models) == []


def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "RESPONSE_CACHE_PATH", tmp_path / "cache.json")
    key = cache.make_key("sys", "prompt", "hash1")
    assert cache.get(key) is None
    cache.set(key, "the response")
    assert cache.get(key) == "the response"
    # Different project_hash → different key → miss.
    other_key = cache.make_key("sys", "prompt", "hash2")
    assert cache.get(other_key) is None


class _FakeProvider:
    """Fails for every model in `fail_models`, succeeds otherwise."""
    def __init__(self, fail_models, rate_limited_models=()):
        self.fail_models = set(fail_models)
        self.rate_limited_models = set(rate_limited_models)
        self.calls = []

    def chat(self, model, messages, timeout):
        self.calls.append(model)
        if model in self.rate_limited_models:
            raise RateLimitedError(f"{model} rate limited")
        if model in self.fail_models:
            raise ProviderError(f"{model} failed")
        return f"response from {model}"


def test_fallback_succeeds_on_third_model():
    provider = _FakeProvider(fail_models=["m1"], rate_limited_models=["m2"])
    text, used, attempts = run_with_fallback(
        provider, ["m1", "m2", "m3"], messages=[{"role": "user", "content": "hi"}], timeout=5,
    )
    assert used == "m3"
    assert text == "response from m3"
    assert [a["model"] for a in attempts] == ["m1", "m2", "m3"]
    assert attempts[0]["outcome"] == "failed"
    assert attempts[1]["outcome"] == "rate_limited"
    assert attempts[2]["outcome"] == "success"


def test_fallback_raises_when_all_models_fail():
    provider = _FakeProvider(fail_models=["m1", "m2"])
    try:
        run_with_fallback(provider, ["m1", "m2"], messages=[], timeout=5)
        assert False, "expected NoFreeModelAvailable"
    except NoFreeModelAvailable as e:
        assert "No free coding model is currently available" in str(e)
        assert e.attempts == ["m1", "m2"]


def test_fallback_raises_immediately_on_empty_model_list():
    provider = _FakeProvider(fail_models=[])
    try:
        run_with_fallback(provider, [], messages=[], timeout=5)
        assert False, "expected NoFreeModelAvailable"
    except NoFreeModelAvailable:
        pass


if __name__ == "__main__":
    import inspect
    fails = 0
    tests = {name: fn for name, fn in globals().items() if name.startswith("test_")}
    for name, fn in tests.items():
        sig = inspect.signature(fn)
        try:
            if "tmp_path" in sig.parameters or "monkeypatch" in sig.parameters:
                print(f"SKIP {name} (needs pytest fixtures — run with `pytest tests/test_ai_router.py`)")
                continue
            fn()
            print(f"PASS {name}")
        except Exception as e:
            fails += 1
            print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
