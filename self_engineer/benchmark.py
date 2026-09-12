"""
Benchmark — measures cheap, reproducible before/after signals for a patch:
  * import time of each changed module (proxy for startup-time impact)
  * peak RSS memory of the import subprocess (proxy for memory footprint)

This deliberately does NOT try to spin up the full JARVIS voice pipeline
(audio devices, Gemini Live session, PyQt6 UI) for a benchmark — that would
be slow, environment-dependent, and unsafe to automate. Import cost is the
cheapest honest signal a static, unattended pipeline can measure for both
"before" (real project) and "after" (sandbox copy) without side effects.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .models import BenchmarkResult

_BENCH_SNIPPET = """
import sys, time, resource
sys.path.insert(0, {root!r})
t0 = time.perf_counter()
try:
    import {module}
except Exception as e:
    print("IMPORT_ERROR", e)
    sys.exit(1)
t1 = time.perf_counter()
peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"IMPORT_TIME_S={{t1 - t0:.4f}}")
print(f"PEAK_RSS_KB={{peak_kb}}")
"""


def _module_name(rel_path: str) -> str | None:
    if not rel_path.endswith(".py"):
        return None
    mod = rel_path[:-3].replace("/", ".").replace("\\", ".")
    if mod.endswith("__init__"):
        mod = mod.rsplit(".", 1)[0]
    return mod or None


def _measure_one(root: Path, module: str, timeout: int = 20) -> dict:
    code = _BENCH_SNIPPET.format(root=str(root), module=module)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"module": module, "error": f"timed out after {timeout}s"}

    out = proc.stdout
    result = {"module": module}
    if "IMPORT_ERROR" in out or proc.returncode != 0:
        result["error"] = (out + proc.stderr).strip()[-300:]
        return result
    for line in out.splitlines():
        if line.startswith("IMPORT_TIME_S="):
            result["import_time_s"] = float(line.split("=", 1)[1])
        elif line.startswith("PEAK_RSS_KB="):
            result["peak_rss_kb"] = int(line.split("=", 1)[1])
    return result


def measure(root: Path, changed_paths: list) -> dict:
    modules = [m for m in (_module_name(p) for p in changed_paths) if m]
    return {m: _measure_one(root, m) for m in modules}


def compare(before_root: Path, after_root: Path, changed_paths: list) -> BenchmarkResult:
    before = measure(before_root, changed_paths)
    after = measure(after_root, changed_paths)
    return BenchmarkResult(before=before, after=after)
