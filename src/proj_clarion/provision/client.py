"""Grafana Cloud client — shells out to `gcx`.

Why gcx instead of httpx + a token:
- gcx is already authenticated against the user's stack via OAuth, so we
  sidestep the access-policy-token-scope dance entirely (no need for
  `grafana-api:write`).
- Keeps a single canonical interface to Cloud across the whole project.
- Lets us continue producing Grafana-API-shaped JSON (dashboards, folders,
  alert rules) and pushes via `gcx api PATH -d @-` (stdin).

If `gcx` is missing on PATH, callers get a clear RuntimeError on first use —
nothing is attempted via HTTP+token as a fallback.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any


class GrafanaClient:
    """Subprocess wrapper around `gcx api`. Same .get/.post/.put/.delete
    surface as the previous httpx-backed client.
    """

    def __init__(self, *, gcx_binary: str = "gcx") -> None:
        if not shutil.which(gcx_binary):
            raise RuntimeError(
                f"`{gcx_binary}` not found on PATH. Provisioning uses gcx for auth; "
                "install it (https://grafana.com/cloud/cli/) or pass --no-push and "
                "push the generated JSON manually."
            )
        self._gcx = gcx_binary

    def close(self) -> None:
        # Subprocess; nothing to close.
        return

    def __enter__(self) -> GrafanaClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ----- HTTP verbs -----

    def get(self, path: str, *, allow_404: bool = False) -> dict[str, Any] | list[Any] | None:
        return self._invoke("GET", path, allow_404=allow_404)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._invoke("POST", path, body=body) or {}

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._invoke("PUT", path, body=body) or {}

    def delete(self, path: str, *, allow_404: bool = True) -> None:
        self._invoke("DELETE", path, allow_404=allow_404)

    # ----- internals -----

    def _invoke(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        """Run `gcx api PATH -X METHOD [-d @-]` and parse JSON output.

        In --agent mode, errors come back on STDOUT as a JSON envelope:
            {"error": {"summary": "HTTP 404", "exitCode": 1, "details": "..."}}
        We detect that shape and route to the right exception (or return None
        for tolerated 404s).
        """
        cmd = [self._gcx, "api", path, "-X", method, "--agent"]
        stdin: bytes | None = None
        if body is not None:
            cmd.extend(["-d", "@-"])
            stdin = json.dumps(body).encode()

        # gcx puts payload on stdout, progress on stderr — never merge.
        result = subprocess.run(  # noqa: S603 — argv-form, no shell expansion
            cmd, input=stdin, capture_output=True, check=False,
        )
        out_raw = result.stdout.decode(errors="replace").strip()
        err_text = result.stderr.decode(errors="replace")

        # Try to parse stdout first; gcx --agent emits JSON for both success and error
        parsed: Any = None
        if out_raw:
            try:
                parsed = json.loads(out_raw)
            except json.JSONDecodeError:
                parsed = None

        if isinstance(parsed, dict) and "error" in parsed and isinstance(parsed["error"], dict):
            err = parsed["error"]
            summary = str(err.get("summary", ""))
            details = str(err.get("details", ""))
            blob = f"{summary} {details}".lower()
            if allow_404 and ("404" in blob or "not found" in blob or "not-found" in blob):
                return None
            if "401" in blob or "403" in blob or "unauthorized" in blob:
                raise GrafanaAuthError(f"{method} {path}: {summary} — {details[:200]}")
            raise GrafanaApiError(f"{method} {path}: {summary} — {details[:300]}")

        if result.returncode != 0:
            # Non-zero exit but no parseable error envelope — surface what we have
            if allow_404 and ("404" in err_text or "not found" in err_text.lower()):
                return None
            raise GrafanaApiError(
                f"{method} {path} failed via gcx (exit {result.returncode}): "
                f"stdout={out_raw[:200]} stderr={err_text[:200]}"
            )

        return parsed


class GrafanaApiError(RuntimeError):
    """gcx api returned a non-2xx response, or output was unparseable."""


class GrafanaAuthError(GrafanaApiError):
    """401/403 — unusual since gcx is OAuth'd; usually means the OAuth
    session expired (`gcx login`) or the resource is in another stack.
    """
