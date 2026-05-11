#!/usr/bin/env bash
#
# Bring up the cloud-forwarding stack (Postgres + PDC + Alloy) in one go,
# automatically deriving Mode-A vars (`GRAFANA_CLOUD_OTLP_*`) from the
# existing Mode-B vars (`OTEL_EXPORTER_OTLP_*`) you already have in `.env`.
#
# Why: the user shouldn't need to maintain TWO copies of their Cloud
# credentials. The Mode-B values (`OTEL_EXPORTER_OTLP_ENDPOINT` and
# `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<base64>`) carry
# everything Alloy needs; we just have to decode the URL-encoded
# Authorization header and strip the `Authorization=` prefix.
#
# Idempotent — safe to re-run; brings up missing services, leaves
# already-running ones alone.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "ERROR: .env not found at $(pwd)/.env" >&2
  exit 1
fi

# shellcheck source=/dev/null
set -a && . ./.env && set +a

# Mode-A endpoint = Mode-B endpoint (same URL, different protocol context)
export GRAFANA_CLOUD_OTLP_ENDPOINT="${GRAFANA_CLOUD_OTLP_ENDPOINT:-${OTEL_EXPORTER_OTLP_ENDPOINT:-}}"

# Mode-A auth = `Authorization` value out of `OTEL_EXPORTER_OTLP_HEADERS`,
# URL-decoded. The headers var is `Authorization=Basic%20<base64>`; we
# want just `Basic <base64>` for Alloy's `otelcol.auth.headers.cloud`.
if [ -z "${GRAFANA_CLOUD_OTLP_AUTH:-}" ] && [ -n "${OTEL_EXPORTER_OTLP_HEADERS:-}" ]; then
  GRAFANA_CLOUD_OTLP_AUTH="$(python3 - <<'PY'
import os, urllib.parse, sys
hv = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
for pair in hv.split(","):
    pair = pair.strip()
    if pair.lower().startswith("authorization="):
        print(urllib.parse.unquote(pair.split("=", 1)[1]))
        sys.exit(0)
sys.exit(1)
PY
)"
  export GRAFANA_CLOUD_OTLP_AUTH
fi

if [ -z "${GRAFANA_CLOUD_OTLP_ENDPOINT:-}" ] || [ -z "${GRAFANA_CLOUD_OTLP_AUTH:-}" ]; then
  echo "ERROR: could not derive Cloud OTLP creds from .env" >&2
  echo "       Need either GRAFANA_CLOUD_OTLP_{ENDPOINT,AUTH} OR" >&2
  echo "       OTEL_EXPORTER_OTLP_{ENDPOINT,HEADERS} (Mode-B) set." >&2
  exit 1
fi

# Standardise asserts.* defaults so Resource attrs match the v0.6 model.
export CLARION_ASSERTS_ENV="${CLARION_ASSERTS_ENV:-prod}"
export CLARION_ASSERTS_SITE="${CLARION_ASSERTS_SITE:-demo}"

echo "→ bringing up Postgres + PDC + Alloy (cloud profile)"
echo "  GRAFANA_CLOUD_OTLP_ENDPOINT=${GRAFANA_CLOUD_OTLP_ENDPOINT}"
echo "  GRAFANA_CLOUD_OTLP_AUTH=Basic <REDACTED, $((${#GRAFANA_CLOUD_OTLP_AUTH} - 6)) chars after 'Basic '>"
echo "  asserts.env=${CLARION_ASSERTS_ENV}  asserts.site=${CLARION_ASSERTS_SITE}"

docker compose -f deploy/docker/compose.yaml --profile cloud up -d

echo ""
echo "→ status:"
docker compose -f deploy/docker/compose.yaml ps
