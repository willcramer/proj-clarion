"""Live-tail Postgres `business_events` rows into Grafana Cloud Loki.

The shape:
    business_events table  →  poll loop (this module)  →  OTLP/HTTP logs
                                                        →  Alloy (localhost:4318)
                                                        →  Grafana Cloud Loki

Why poll instead of LISTEN/NOTIFY: events.event_id is BIGSERIAL, so a
`WHERE event_id > $cursor ORDER BY event_id LIMIT $batch` query is O(batch)
on the primary-key index. NOTIFY would shave latency but needs a trigger on
insert and a long-lived libpq connection — not worth the complexity for v0.5.

Cursor persistence: stored at `data/livetail/<plan_id>.cursor` so a restart
resumes where it left off. One cursor per plan.
"""

from proj_clarion.livetail.runner import LiveTailer, run_livetail

__all__ = ["LiveTailer", "run_livetail"]
