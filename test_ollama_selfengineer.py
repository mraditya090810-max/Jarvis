"""
Standalone smoke test for:
  1. Ollama auto-start (no need to run 'ollama serve' yourself first)
  2. The self-engineering pipeline actually generating a patch via Ollama

Run from the Jarvis project root:

    python test_ollama_selfengineer.py

Does NOT require the PyQt6/audio/GUI stack — only the self_engineer and
core.ai packages, which is exactly what main.py's self_engineer tool calls
under the hood.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))


def test_1_ollama_autostart_and_router():
    print("\n=== TEST 1: Ollama auto-start + core.ai router ===")
    from core.ai import call_llm_text

    print("Calling call_llm_text()... if Ollama isn't already running, you "
          "should see '[LLM] Ollama not running — launching \\'ollama serve\\'…' below.")
    text = call_llm_text(
        "Reply with exactly the word: PONG",
        system="You are a terse test responder.",
        timeout=90,
        task="manual_smoke_test",
    )
    print(f"Model replied: {text!r}")
    assert text.strip(), "Empty response from the router — see console output above for the cause."

    # Confirm the log actually shows an 'ollama/...' model was used, not OpenRouter.
    log_path = BASE_DIR / "memory" / "ai_router_logs.jsonl"
    if log_path.exists():
        last_line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        print(f"Last router log entry: {last_line}")
        if '"model": "ollama/' in last_line:
            print("✅ Confirmed: this call was served by local Ollama.")
        else:
            print("⚠️  This call did NOT use Ollama (check provider_mode in "
                  "config/ai_router.json, and that Ollama + the model are installed).")
    print("TEST 1 PASSED")


def test_2_self_engineer_end_to_end():
    print("\n=== TEST 2: self-engineering analyze -> propose (uses Ollama for the patch) ===")
    from self_engineer.orchestrator import SelfEngineer

    eng = SelfEngineer(project_root=BASE_DIR)

    weaknesses = eng.analyze()
    print(f"analyze(): found {len(weaknesses)} weaknesses")
    if not weaknesses:
        print("No weaknesses found in this copy of the project — nothing to propose a patch for. "
              "TEST 2 SKIPPED (this is not a failure).")
        return

    w = weaknesses[0]
    print(f"Proposing a fix for: {w.id} [{w.category}] {w.file}:{w.line_start} — {w.description}")

    patch = eng.propose(w.id)
    print(f"propose(): status={patch.status.value}")
    print(eng.get_report(patch.id))

    if patch.status.value == "awaiting_approval":
        print(f"✅ Patch {patch.id} passed static analysis/tests/security and is ready for review.")
        print(f"   To apply it for real: eng.approve('{patch.id}')")
        print(f"   To discard it:        eng.reject('{patch.id}')")
    else:
        print(f"Patch ended in status '{patch.status.value}' — see the report above for why "
              "(this can be a normal outcome, e.g. FAILED_TESTS on a real regression, or "
              "BLOCKED_SECURITY on a protected file).")
    print("TEST 2 PASSED (pipeline ran end-to-end)")


if __name__ == "__main__":
    test_1_ollama_autostart_and_router()
    test_2_self_engineer_end_to_end()
    print("\nAll smoke tests completed.")
