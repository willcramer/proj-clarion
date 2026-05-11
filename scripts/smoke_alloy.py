"""One-shot smoke test for the Mode-A (Python → Alloy → Cloud) path.

Bypasses the CLI's `load_dotenv(override=True)` so we can force
OTEL_EXPORTER_OTLP_ENDPOINT to point at the local Alloy instance even when
.env has Mode-B values in it. After the run, the local .env is untouched.

Usage:
    uv run python scripts/smoke_alloy.py <plan_id> [--seconds N]

What it does:
    1. Pin OTEL_EXPORTER_OTLP_ENDPOINT to http://localhost:4318 (Alloy)
       and clear OTEL_EXPORTER_OTLP_HEADERS (Alloy doesn't need auth)
    2. Look up the plan in Postgres
    3. Spin up the EntityEmitter (metrics + RED + synthetic logs) and a
       LiveTailer (business_events → OTLP logs) — both pushing to Alloy
    4. Run for `--seconds` (default 90, ~3× the 30s metric export interval),
       then shut down cleanly
    5. Print where to look in Grafana Cloud

Each emitted record carries:
    - resource attribute `clarion.plan_id` = the plan's UUID
    - attribute `alloy.routed=true` injected by Alloy's processor.attributes
      so we can grep for it on the Cloud side
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────
# Override env BEFORE importing anything that reads it. The CLI's
# observability bootstrap reads OTEL_* at import time via OpenLIT.
# ─────────────────────────────────────────────────────────────────────
from dotenv import load_dotenv

# Load .env first (Mode B values), then override the two we need for Mode A.
# .env on disk stays unmodified.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318"
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = ""
os.environ.setdefault("CLARION_ASSERTS_ENV", "prod")
os.environ.setdefault("CLARION_ASSERTS_SITE", "demo")

# ── now import the rest ──────────────────────────────────────────────
from proj_clarion.kg_publish import EntityEmitter, expand_with_synthetic_infra
from proj_clarion.livetail.runner import LiveTailer
from proj_clarion.storage import PlanRepo, session_scope


def _resolve_plan_id(prefix: str) -> str:
    from sqlalchemy import text

    with session_scope() as s:
        row = s.execute(
            text("SELECT plan_id FROM demo_plans WHERE plan_id::text LIKE :p LIMIT 1"),
            {"p": f"{prefix}%"},
        ).fetchone()
        if not row:
            raise SystemExit(f"No plan matches prefix {prefix!r}")
        return str(row[0])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("plan_id", help="Plan UUID or unambiguous prefix")
    p.add_argument("--seconds", type=int, default=90,
                   help="How long to run (default 90, > 30s metric export interval)")
    args = p.parse_args()

    full_plan_id = _resolve_plan_id(args.plan_id)
    print(f"[smoke] resolved plan_id = {full_plan_id}")

    with session_scope() as s:
        plan = PlanRepo().get(s, full_plan_id)
    if plan is None:
        raise SystemExit(f"Plan {full_plan_id} not found")
    expanded = expand_with_synthetic_infra(plan)

    # ── EntityEmitter on the foreground via threads ─────────────────────
    print(f"[smoke] starting EntityEmitter ({len(expanded.nodes)} entities) "
          f"→ http://localhost:4318")
    emitter = EntityEmitter(
        plan, expanded,
        customer="acme_retail-v2",  # distinct customer label so we can grep just our test
        env="prod",
        site="demo",
        export_interval_seconds=15,  # tighter than default to land within --seconds
    )
    emitter.start()

    # ── LiveTailer on a background thread ──────────────────────────────
    tailer = LiveTailer(
        full_plan_id,
        customer="acme_retail-v2",
        batch_size=200,
        poll_interval_seconds=1.0,
    )
    livetail_thread = threading.Thread(target=tailer.run, daemon=True)
    print(f"[smoke] starting LiveTailer → http://localhost:4318")
    livetail_thread.start()

    try:
        print(f"[smoke] running for {args.seconds}s; ctrl-C to bail early")
        for i in range(args.seconds):
            time.sleep(1)
            if i % 15 == 14:
                print(f"[smoke]  +{i+1}s  livetail.rows_emitted={tailer.stats.rows_emitted}")
    except KeyboardInterrupt:
        print("[smoke] interrupted")
    finally:
        print("[smoke] shutting down emitters")
        tailer.stop()
        livetail_thread.join(timeout=5)
        emitter.stop()

    print("\n[smoke] done. Verify in Grafana Cloud:")
    print(f"  metrics:  count by (alloy_routed)(clarion_entity_info"
          f'{{clarion_customer="acme_retail-v2"}})')
    print(f"  logs:     {{service_name=\"proj-clarion-livetail\","
          f"clarion_customer=\"acme_retail-v2\"}}")
    print(f"  traces:   resource.service.namespace = \"proj-clarion\" && "
          f"resource.clarion.customer = \"acme_retail-v2\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
