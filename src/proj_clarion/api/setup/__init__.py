"""First-run setup module.

Goal: anyone who clones the Clarion template repo can stand up a working
demo from a browser, without shell editing of `.env`. The flow:

    blank .env  →  API boots in "setup-required" mode  →  every
    non-`/api/setup/*` route returns 503 with X-Clarion-Setup: required
    →  UI sees that header on first load, renders the Setup page
    →  user pastes / uploads tokens, hits Validate, then Save
    →  API writes `.env` atomically (with a `.env.bak` backup), reloads
        os.environ in-process, gate flips, app becomes accessible.

Submodules:
  - `schema`      — definitive list of supported env keys with metadata
                    (group, required, description, how-to-find URL).
  - `parsers`     — accepts `.env` / shell-export / JSON / free-form
                    text and emits a normalized {KEY: VALUE} map.
  - `validators`  — per-key live validators that test a candidate value
                    against the real service (e.g. Anthropic, Grafana
                    Cloud). Never logs or echoes the value back.
  - `persistence` — atomic `.env` writer with backup, in-process
                    `os.environ` refresh after save.
"""

from proj_clarion.api.setup.persistence import (
    is_setup_complete,
    refresh_environment,
    save_env,
)
from proj_clarion.api.setup.schema import (
    REQUIRED_KEYS,
    SETUP_KEYS,
    SetupKey,
    check_status,
)

__all__ = [
    "REQUIRED_KEYS",
    "SETUP_KEYS",
    "SetupKey",
    "check_status",
    "is_setup_complete",
    "refresh_environment",
    "save_env",
]
