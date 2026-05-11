#!/usr/bin/env python3
"""Cross-vertical smoke test for the first three pipeline phases.

The planner agent's prompts are vertical-aware but its output is whatever
the LLM decides to emit — and it routinely invents enum values for
verticals we haven't pre-thought of (ITC: 'plant', healthcare: 'clinic').
Until each new vertical is in the test set, the first build is the test.

This script catches that early. For each URL in the list:
  1. research (URL → CompanyProfile)
  2. plan run (profile → DemoPlan with KG, dashboards, alerts, tools)
  3. plan approve (state transition only)

It does NOT generate events, push dashboards, or publish KG — those are
expensive cloud-side and the user's stack would fill up quickly. The
goal is to pressure-test the first three phases across diverse verticals
so we discover schema/sanitizer gaps locally.

Run:
    just smoke-test-industries          # default URL list
    just smoke-test-industries url1 url2 ...  # custom list

Output: a per-URL pass/fail table + cumulative summary. Exit code is
non-zero if any URL hit a hard failure (so this can gate CI later).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# Default vertical mix. Each must match RESEARCH_ALLOWED_HOSTS in .env
# or the research phase refuses to fetch. Edit .env, not this list, to
# allow new domains.
DEFAULT_URLS = [
    # Retail / omnichannel
    "https://www.acme_retail.com",
    # SaaS / B2B
    "https://www.erp-vendor.example",
    "https://grafana.com",
    # Tech / observability
    "https://www.cisco.com",
    "https://www.splunk.com",
    # Enterprise
    "https://www.microsoft.com",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = PROJECT_ROOT / "data" / "profiles"


def _run_cli(*argv: str, timeout: int = 600) -> tuple[int, str]:
    """Run a CLI subprocess, capturing combined stdout/stderr. Returns
    (returncode, output_tail). Tail is bounded so we don't blow log
    space when a phase produces megabytes of structlog output."""
    cmd = ["uv", "run", "python", "-m", "proj_clarion.cli.main", *argv]
    proc = subprocess.run(
        cmd, cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.splitlines()[-30:])
    return proc.returncode, tail


def _newest_profile_for(url: str) -> Path | None:
    """Find the most recently-written profile JSON. We assume it's the
    one we just produced because the test runs serially."""
    if not PROFILES_DIR.exists():
        return None
    candidates = sorted(PROFILES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _newest_plan_id_for_profile(profile_id: str) -> str | None:
    """Read the plan_id back from disk after `plan run` writes it.
    Could query Postgres but disk is good enough for a smoke test."""
    plans_dir = PROJECT_ROOT / "data" / "plans"
    if not plans_dir.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    for f in plans_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("source_profile_id") == profile_id:
                candidates.append((f.stat().st_mtime, f))
        except (json.JSONDecodeError, OSError):
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1].stem


def smoke_test_one(url: str) -> dict[str, object]:
    """Run research → plan → approve. Returns a result dict with
    per-phase status, the produced IDs, and (on failure) a tail of
    output for diagnosis."""
    result: dict[str, object] = {
        "url": url,
        "research": "skip",
        "plan": "skip",
        "approve": "skip",
        "profile_id": None,
        "plan_id": None,
        "duration_s": 0,
        "fail_phase": None,
        "fail_tail": "",
    }
    started = time.monotonic()

    # Phase 1: research
    print(f"  → research…", end=" ", flush=True)
    rc, tail = _run_cli("research", url, timeout=240)
    if rc != 0:
        result["research"] = "fail"
        result["fail_phase"] = "research"
        result["fail_tail"] = tail
        result["duration_s"] = round(time.monotonic() - started, 1)
        print("FAIL")
        return result
    result["research"] = "pass"
    print("ok", end=" ", flush=True)

    profile_path = _newest_profile_for(url)
    if profile_path is None:
        result["fail_phase"] = "research"
        result["fail_tail"] = "research returned 0 but no profile JSON appeared on disk"
        result["duration_s"] = round(time.monotonic() - started, 1)
        print("FAIL (no profile written)")
        return result
    result["profile_id"] = profile_path.stem

    # Phase 2: plan run
    print(f"→ plan…", end=" ", flush=True)
    rc, tail = _run_cli("plan", "run", str(profile_path), timeout=480)
    if rc != 0:
        result["plan"] = "fail"
        result["fail_phase"] = "plan"
        result["fail_tail"] = tail
        result["duration_s"] = round(time.monotonic() - started, 1)
        print("FAIL")
        return result
    result["plan"] = "pass"
    print("ok", end=" ", flush=True)

    plan_id = _newest_plan_id_for_profile(profile_path.stem)
    if plan_id is None:
        result["fail_phase"] = "plan"
        result["fail_tail"] = "plan run returned 0 but no plan JSON found for this profile"
        result["duration_s"] = round(time.monotonic() - started, 1)
        print("FAIL (no plan written)")
        return result
    result["plan_id"] = plan_id

    # Phase 3: plan approve
    print(f"→ approve…", end=" ", flush=True)
    rc, tail = _run_cli(
        "plan", "approve", plan_id,
        "--note", "smoke-test", "--actor", "smoke-test",
        timeout=30,
    )
    if rc != 0:
        result["approve"] = "fail"
        result["fail_phase"] = "approve"
        result["fail_tail"] = tail
        result["duration_s"] = round(time.monotonic() - started, 1)
        print("FAIL")
        return result
    result["approve"] = "pass"
    result["duration_s"] = round(time.monotonic() - started, 1)
    print("ok")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "urls", nargs="*",
        help=f"URLs to test (default: {len(DEFAULT_URLS)} verticals)",
    )
    parser.add_argument(
        "--bail-on-fail", action="store_true",
        help="Stop on the first failure; useful when iterating on sanitizers.",
    )
    args = parser.parse_args()

    urls = args.urls or DEFAULT_URLS
    print(f"Smoke testing research → plan → approve across {len(urls)} URLs:\n")
    results: list[dict[str, object]] = []

    for url in urls:
        print(f"[{url}]")
        try:
            r = smoke_test_one(url)
        except subprocess.TimeoutExpired as exc:
            r = {
                "url": url, "research": "?", "plan": "?", "approve": "?",
                "profile_id": None, "plan_id": None, "duration_s": exc.timeout,
                "fail_phase": "timeout", "fail_tail": str(exc),
            }
            print("  TIMEOUT")
        results.append(r)
        if args.bail_on_fail and r.get("fail_phase"):
            break

    # Summary
    print("\n" + "=" * 78)
    print(f"{'URL':<32} {'Research':>9} {'Plan':>5} {'Approve':>8} {'Sec':>6}")
    print("-" * 78)
    for r in results:
        url = str(r["url"])
        print(
            f"{url[:32]:<32} "
            f"{str(r['research']):>9} {str(r['plan']):>5} "
            f"{str(r['approve']):>8} {str(r['duration_s']):>6}"
        )
    print("=" * 78)

    failures = [r for r in results if r.get("fail_phase")]
    print(f"\n{len(results) - len(failures)}/{len(results)} passed all three phases.")
    for r in failures:
        print(f"\n--- {r['url']} failed at {r['fail_phase']} ---")
        tail = str(r.get("fail_tail", "")).strip()
        print(tail[-1500:] if len(tail) > 1500 else tail)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
