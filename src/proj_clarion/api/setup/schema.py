"""Definitive list of env keys Clarion knows about.

Each `SetupKey` carries:
  - `key`         — env var name
  - `group`       — UI grouping (anthropic | grafana_cloud | sigil | pdc | advanced)
  - `required`    — must be present for setup to be "complete"
  - `label`       — human-readable name in the form
  - `description` — one-line hint shown under the input
  - `placeholder` — example value
  - `help_url`    — where to find this token / value
  - `secret`      — if True, mask in the UI by default
  - `validator`   — name of a validator function in `validators.py` (or
                    None for "no live validation, just sanity-check the
                    format")

Add new keys here; the UI and parsers pick them up automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

Group = Literal["anthropic", "grafana_cloud", "sigil", "pdc", "advanced"]


@dataclass(frozen=True)
class SetupKey:
    key: str
    group: Group
    required: bool
    label: str
    description: str
    placeholder: str = ""
    help_url: str = ""
    secret: bool = False
    validator: str | None = None


# Listed in the order they should appear in the UI: most important first,
# advanced/optional last. Grouping is by `group` field — the UI renders
# one card per group.
SETUP_KEYS: tuple[SetupKey, ...] = (
    # ─── Anthropic (required) ──────────────────────────────────────
    SetupKey(
        key="ANTHROPIC_API_KEY",
        group="anthropic",
        required=True,
        label="Anthropic API key",
        description="Powers the research + planner agents. Starts with `sk-ant-`.",
        placeholder="sk-ant-api03-…",
        help_url="https://console.anthropic.com/settings/keys",
        secret=True,
        validator="anthropic_api_key",
    ),
    SetupKey(
        key="ANTHROPIC_MODEL",
        group="anthropic",
        required=False,
        label="Anthropic model",
        description="Defaults to claude-opus-4-7. Leave blank to use the default.",
        placeholder="claude-opus-4-7",
    ),

    # ─── Grafana Cloud (required for visualization) ────────────────
    SetupKey(
        key="GRAFANA_CLOUD_STACK_URL",
        group="grafana_cloud",
        required=True,
        label="Grafana Cloud stack URL",
        description="Your stack's URL — e.g. https://mystack.grafana.net (no trailing slash).",
        placeholder="https://mystack.grafana.net",
        help_url="https://grafana.com/profile/org",
        validator="grafana_stack_url",
    ),
    SetupKey(
        key="GRAFANA_CLOUD_API_TOKEN",
        group="grafana_cloud",
        required=True,
        label="Grafana Cloud API token",
        description="Access-policy token with dashboards / alerts / rules write scopes.",
        placeholder="glc_…",
        help_url="https://grafana.com/orgs/-/access-policies",
        secret=True,
        validator="grafana_cloud_token",
    ),
    SetupKey(
        key="GRAFANA_CLOUD_OTLP_ENDPOINT",
        group="grafana_cloud",
        required=True,
        label="OTLP gateway endpoint",
        description="From Cloud → Connections → OTLP. Looks like https://otlp-gateway-prod-<region>.grafana.net/otlp.",
        placeholder="https://otlp-gateway-prod-<region>.grafana.net/otlp",
        help_url="https://grafana.com/docs/grafana-cloud/send-data/otlp/",
    ),
    SetupKey(
        key="GRAFANA_CLOUD_OTLP_AUTH",
        group="grafana_cloud",
        required=True,
        label="OTLP basic-auth header",
        description=(
            "Full `Authorization` value — "
            "`Basic <base64(instance_id:access_policy_token)>`. The same Cloud OTLP page shows the exact string."
        ),
        placeholder="Basic dGVuYW50LWlkOmdsY18uLi4=",
        secret=True,
    ),
    SetupKey(
        key="GRAFANA_DS_POSTGRES_UID",
        group="grafana_cloud",
        required=False,
        label="Postgres datasource UID",
        description=(
            "UID of the Cloud Postgres datasource that reaches your local DB (via PDC). "
            "Optional but needed for the Postgres-backed business dashboards. "
            "Find with: `gcx datasources list -o json`."
        ),
        placeholder="postgres_pdc_abc123",
    ),
    SetupKey(
        key="GCLOUD_HOSTED_GRAFANA_ID",
        group="grafana_cloud",
        required=False,
        label="Hosted Grafana ID",
        description="Numeric stack id from `https://grafana.com/orgs/<org>/stacks`. Needed for `gcx` CLI auth.",
        placeholder="1234567",
    ),

    # ─── Sigil / AI observability (optional) ────────────────────────
    SetupKey(
        key="SIGIL_ENDPOINT",
        group="sigil",
        required=False,
        label="Sigil endpoint",
        description="Cloud → AI Observability → Configuration. Leave empty to disable Sigil entirely.",
        placeholder="https://sigil-prod-<region>.grafana.net",
    ),
    SetupKey(
        key="SIGIL_AUTH_TENANT_ID",
        group="sigil",
        required=False,
        label="Sigil tenant id",
        description="Numeric tenant ID from the same Cloud page.",
        placeholder="1234567",
    ),
    SetupKey(
        key="SIGIL_AUTH_TOKEN",
        group="sigil",
        required=False,
        label="Sigil access token",
        description="Same access-policy token works if it has `sigil:write` scope.",
        placeholder="glc_…",
        secret=True,
    ),

    # ─── PDC / private datasource (optional) ─────────────────────────
    SetupKey(
        key="GCLOUD_PDC_CLUSTER",
        group="pdc",
        required=False,
        label="PDC cluster ID",
        description="Only needed if you're using Private Datasource Connect for Postgres.",
        placeholder="prod-us-east-0",
    ),
    SetupKey(
        key="GCLOUD_PDC_SIGNING_TOKEN",
        group="pdc",
        required=False,
        label="PDC signing token",
        description="From Cloud → Connections → Private Data Source Connect.",
        placeholder="",
        secret=True,
    ),
)


# Keys that MUST be present for the app to leave setup mode. Anything
# not in this set is optional and the setup UI can skip it.
REQUIRED_KEYS: frozenset[str] = frozenset(
    k.key for k in SETUP_KEYS if k.required
)

# Map for quick lookup
SETUP_KEYS_BY_KEY: dict[str, SetupKey] = {k.key: k for k in SETUP_KEYS}


def check_status() -> dict[str, object]:
    """Snapshot of which keys are present in the current process env.

    Returns:
        {
          "ready": bool,                        # all required keys present
          "missing": [str, ...],                # required keys not set
          "present": [str, ...],                # keys with non-empty values
          "groups": {group: {"required":int, "present":int, "missing":[...]}, ...},
        }

    Values themselves are NEVER returned — only key presence. The setup
    UI uses this to render badges (e.g. "Anthropic ✓ / Grafana ✗").
    """
    present: list[str] = []
    missing: list[str] = []
    for k in SETUP_KEYS:
        val = (os.environ.get(k.key) or "").strip()
        if val:
            present.append(k.key)
        elif k.required:
            missing.append(k.key)

    # Per-group rollup
    groups: dict[str, dict[str, object]] = {}
    for k in SETUP_KEYS:
        g = groups.setdefault(k.group, {"required": 0, "present": 0, "missing": []})
        if k.required:
            g["required"] = int(g["required"]) + 1  # type: ignore[arg-type]
            if k.key in missing:
                missing_list = g["missing"]
                assert isinstance(missing_list, list)
                missing_list.append(k.key)
        if k.key in present:
            g["present"] = int(g["present"]) + 1  # type: ignore[arg-type]

    return {
        "ready":   len(missing) == 0,
        "missing": missing,
        "present": present,
        "groups":  groups,
    }
