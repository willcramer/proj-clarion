"""Resolve Mode-A Cloud OTLP creds from whatever the user has in `.env`.

If `GRAFANA_CLOUD_OTLP_*` are explicitly set, use them.
Otherwise derive from `OTEL_EXPORTER_OTLP_*` (Mode-B), which is what the
existing v0.4-era `.env.example` documented and what most users will have.

Returned dict is meant to be merged into the env dict of a subprocess
(or a child docker compose call). Returns None if nothing usable was
found — caller should treat that as "Cloud forwarding not available".
"""

from __future__ import annotations

import os
import urllib.parse


def resolve_cloud_creds() -> dict[str, str] | None:
    """Return env-var dict suitable for `subprocess.run(..., env=...)`,
    or None if no creds are derivable.

    Always sets:
        GRAFANA_CLOUD_OTLP_ENDPOINT
        GRAFANA_CLOUD_OTLP_AUTH        (`Basic <base64>`, decoded)
        CLARION_ASSERTS_ENV            (default 'prod')
        CLARION_ASSERTS_SITE           (default 'demo')
    """
    endpoint = os.environ.get("GRAFANA_CLOUD_OTLP_ENDPOINT") \
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") \
        or ""
    auth = os.environ.get("GRAFANA_CLOUD_OTLP_AUTH") or ""

    if not auth and (raw_headers := os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")):
        # Format: `Authorization=Basic%20<base64>` (possibly comma-separated
        # for multiple headers). Find the Authorization pair, strip the
        # `Authorization=` prefix, URL-decode the value (turns `%20` into ` `).
        for pair in raw_headers.split(","):
            pair = pair.strip()
            if pair.lower().startswith("authorization="):
                auth = urllib.parse.unquote(pair.split("=", 1)[1])
                break

    if not endpoint or not auth:
        return None

    return {
        "GRAFANA_CLOUD_OTLP_ENDPOINT": endpoint,
        "GRAFANA_CLOUD_OTLP_AUTH":     auth,
        "CLARION_ASSERTS_ENV":         os.environ.get("CLARION_ASSERTS_ENV", "dev"),
        "CLARION_ASSERTS_SITE":        os.environ.get("CLARION_ASSERTS_SITE", "demo"),
    }
