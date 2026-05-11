"""Parsers for env-var input.

Accepts four input shapes — whichever produces a non-empty key-value map
wins. Order tried (most-specific first):

  1. JSON object — `{ "KEY": "VALUE", ... }`
  2. `.env` / shell — `KEY=VALUE` or `export KEY=VALUE` per line
  3. "Plain" key:value per line — `KEY: VALUE` (yaml-ish)

CSV deliberately not supported — env-var values commonly contain `,`
characters (especially OTLP `Authorization` headers, base64-encoded
values), and disambiguating which-column-is-key vs which-column-is-value
makes the UX worse than the gains.

All parsers return `dict[str, str]`. Unknown keys are kept as-is; the
caller (`/api/setup/save`) decides whether to accept or reject them.
"""

from __future__ import annotations

import json
import re
from typing import Callable


_ENV_LINE_RE = re.compile(
    # Optional leading `export `, then a valid env-var name, then `=`,
    # then everything until end of line. Captures key + value.
    r"""^\s*(?:export\s+)?            # optional `export `
        ([A-Za-z_][A-Za-z0-9_]*)      # capture: KEY
        \s*=\s*                        # =
        (.*?)\s*$                      # capture: VALUE (trailing-ws trimmed)
    """,
    re.VERBOSE,
)

_YAML_ISH_LINE_RE = re.compile(
    r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$""",
)


def _strip_quotes(value: str) -> str:
    """Strip a single pair of surrounding quotes if present.

    `KEY="some thing"` → `some thing`. `KEY='x'` → `x`. Leaves
    unquoted or mismatched-quote values alone — better to keep a
    surprising-looking value than to drop characters silently.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_env_text(text: str) -> dict[str, str]:
    """Parse `KEY=VALUE` / `export KEY=VALUE` lines. Skips comments and
    blank lines. Last-write-wins for duplicate keys."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if m:
            key, value = m.group(1), _strip_quotes(m.group(2))
            out[key] = value
    return out


def _parse_yaml_ish(text: str) -> dict[str, str]:
    """Parse `KEY: VALUE` lines. Used as a fallback when no `=` separator
    was found — handy for users pasting docs-style key listings."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _YAML_ISH_LINE_RE.match(line)
        if m:
            key, value = m.group(1), _strip_quotes(m.group(2))
            out[key] = value
    return out


def _parse_json_text(text: str) -> dict[str, str]:
    """Parse JSON object with string values. Coerces non-string scalar
    values to string (e.g. `{"PORT": 5432}` → `{"PORT": "5432"}`).
    Returns empty on parse error — caller falls through to next parser."""
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in loaded.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, (str, int, float, bool)):
            out[key] = str(value)
    return out


# Parser-cascade. The first parser to return ≥1 key wins; everything
# falls back to env-style which is the most permissive.
_PARSER_CASCADE: tuple[Callable[[str], dict[str, str]], ...] = (
    _parse_json_text,
    _parse_env_text,
    _parse_yaml_ish,
)


def parse_user_input(text: str) -> dict[str, str]:
    """Parse free-form user input into a {KEY: VALUE} map.

    Tries JSON first (in case the user pasted a JSON config), then `.env`
    / shell-style, then yaml-style `KEY: VALUE`. Returns the first map
    that has at least one entry. If none parse, returns `{}` — the caller
    should treat that as "no recognizable input" and show a hint.
    """
    text = text.strip()
    if not text:
        return {}
    for parser in _PARSER_CASCADE:
        parsed = parser(text)
        if parsed:
            return parsed
    return {}
