"""
Ranks the free models returned by model_discovery so the router always tries
the *best* free model first, then falls back down the list.

No model id is ever hardcoded as a hard requirement — this only adjusts the
ranking of whatever OpenRouter happens to be offering as free *today*, so it
keeps working even after the current crop of free models is retired or
renamed. New/unrecognized model families still get a baseline score purely
from context size, so they're never excluded, just not boosted.
"""
from __future__ import annotations

# Family name fragments (matched case-insensitively against the model id)
# that are historically strong at coding tasks. This is a *ranking hint*,
# not a filter — every free model is still eligible.
_CODING_FAMILY_HINTS = (
    "coder", "code", "deepseek", "qwen", "qwen2", "qwen3", "llama-3",
    "llama3", "mistral", "mixtral", "codestral", "starcoder", "phi-3",
    "phi-4", "gemma", "glm", "yi-", "wizardcoder",
)

# Substrings that suggest a model is a small/preview/distilled variant less
# suited to whole-file code generation — mild penalty, not exclusion.
_WEAK_HINTS = ("mini", "tiny", "nano", "1b", "2b", "distill", "preview", "alpha")


def _context_score(model: dict) -> float:
    ctx = model.get("context_length") or model.get("top_provider", {}).get("context_length") or 0
    try:
        ctx = float(ctx)
    except (TypeError, ValueError):
        ctx = 0.0
    # Diminishing returns past ~64k — plenty for a single-file coding prompt.
    return min(ctx, 64_000) / 64_000.0


def _family_score(model_id: str) -> float:
    mid = model_id.lower()
    score = 0.0
    if any(h in mid for h in _CODING_FAMILY_HINTS):
        score += 1.0
    if any(h in mid for h in _WEAK_HINTS):
        score -= 0.5
    return score


def score_model(model: dict) -> float:
    model_id = model.get("id", "")
    return (2.0 * _family_score(model_id)) + (1.0 * _context_score(model))


def rank_models(models: list[dict]) -> list[str]:
    """Returns model ids sorted best-first."""
    scored = sorted(models, key=score_model, reverse=True)
    return [m["id"] for m in scored if m.get("id")]


# Substrings that indicate a model accepts image input (vision/multimodal).
_VISION_HINTS = ("vision", "-vl", "vl-", "multimodal", "-v1", "pixtral", "llava")


def is_vision_capable(model: dict) -> bool:
    model_id = model.get("id", "").lower()
    if any(h in model_id for h in _VISION_HINTS):
        return True
    modality = (model.get("architecture", {}) or {}).get("input_modalities", [])
    return "image" in modality


def rank_vision_models(models: list[dict]) -> list[str]:
    """Same ranking as rank_models, restricted to free models that accept
    image input. Returns [] if OpenRouter currently offers no free vision
    model — callers must treat that like any other 'no free model' case."""
    vision_models = [m for m in models if is_vision_capable(m)]
    return rank_models(vision_models)
