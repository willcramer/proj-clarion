"""Lenient URL normalization for build inputs.

The pipeline takes a customer URL as the entry point. SEs paste this in
from the prospect's signature, a deck, a CRM record — sources that
embed a URL inside whatever the SE happened to copy. Strict validation
(`HttpUrl`) was rejecting common, harmless variants:

  - "sentinel.com"                  (no scheme)
  - "https://sentinel.com/"          (trailing slash)
  - "  HTTPS://SENTINEL.COM "         (whitespace + uppercase scheme)
  - "https://www.sentinel.com/about" (path tail — strip to root)
  - "sentinel.com/products/"         (no scheme + path + trailing slash)

…and failing the whole build with a Pydantic validation error. This
module canonicalises the input and returns either a normalized form OR
a structured warning the UI can surface, instead of crashing the build.

Design choices:
- Always default the scheme to `https://` (the modern web).
- Strip trailing slashes, query strings, and fragments — research only
  needs the company root.
- Strip path tails for the build entry point (a plan is per-company,
  not per-page). The normalizer returns the path it stripped so the UI
  can show "we used `https://sentinel.com` instead of the page you pasted".
- Keep `www.` if present — some sites redirect `naked` domains to their
  www form unreliably; preserving it is safer.
- Reject obviously-bad input (empty, no dot, illegal chars) with a
  *helpful* error message and an example, rather than a stack trace.

The canonical entry point is `normalize_company_url(raw)`, returning a
`NormalizedURL` with the cleaned URL, the original, and a list of
human-readable hints describing what was changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse


@dataclass
class NormalizedURL:
    """Result of normalizing a raw URL string.

    `url` is the canonical form, safe to use downstream. `hints` is a
    list of human-readable notes about what was rewritten — non-empty
    means the user's original input wasn't already canonical, so the UI
    can surface a small "we used X instead" notice. `original` keeps
    the unmodified user input for reference / debugging.
    """

    url: str
    original: str
    hints: list[str] = field(default_factory=list)


class URLValidationError(ValueError):
    """Raised when input can't be coerced into a usable URL.

    The message is intended to be user-readable: the API surfaces it
    verbatim in the 400 response body, and the UI shows it under the URL
    field. Always include a concrete example of what *would* work.
    """


# A URL must contain at least one dot in the host part. We tolerate
# pretty much anything else — schemes are added, ports are kept, paths
# are dropped. Reject only the truly unparseable.
_HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def normalize_company_url(raw: str | None) -> NormalizedURL:
    """Normalize an SE-supplied company URL.

    Accepts almost anything that *looks* like a URL or bare domain;
    rejects only the truly unrecoverable. Returns a `NormalizedURL`.
    Raises `URLValidationError` with a helpful message + example on
    rejection.

    Examples (input → result):
      "sentinel.com"                    → "https://sentinel.com"
      "https://sentinel.com/"           → "https://sentinel.com"
      "  HTTPS://SENTINEL.COM "         → "https://sentinel.com"
      "sentinel.com/products/"          → "https://sentinel.com"  (path stripped)
      "www.sentinel.com"                → "https://www.sentinel.com"
      "https://sentinel.com?utm=foo"    → "https://sentinel.com"
      ""                             → URLValidationError
      "not a url"                    → URLValidationError
    """
    if raw is None:
        raise URLValidationError(
            "URL is required. Example: https://sentinel.com"
        )

    s = raw.strip()
    if not s:
        raise URLValidationError(
            "URL is required. Example: https://sentinel.com"
        )

    hints: list[str] = []

    # Extract from common copy-paste wrappers BEFORE stripping bracket
    # chars, so labeled forms like "Sentinel <https://sentinel.com>" still
    # pull out the URL part.
    md_link = re.search(r"\((https?://[^)\s]+)\)", s)
    angle_link = re.search(r"<\s*((?:https?://)?[\w.-]+(?::\d+)?(?:/[^>]*)?)\s*>", s)
    if md_link:
        s = md_link.group(1)
        hints.append("Extracted URL from a markdown link.")
    elif angle_link:
        s = angle_link.group(1)
        hints.append("Extracted URL from <angle brackets>.")
    else:
        # Strip wrapping whitespace + bare angle brackets (`<sentinel.com>`),
        # plus trailing sentence punctuation (".", ",", ";", ":") that
        # often shows up when copy-pasted from prose.
        s = s.strip("<>").rstrip(".,;:")

    # Default scheme. Accept "http://" too; canonicalise to lowercase.
    parsed = urlparse(s)
    if not parsed.scheme:
        s = "https://" + s
        hints.append("Added missing https:// scheme.")
        parsed = urlparse(s)
    elif parsed.scheme.lower() not in ("http", "https"):
        raise URLValidationError(
            f"Unsupported URL scheme {parsed.scheme!r}. "
            f"Use http:// or https://. Example: https://sentinel.com"
        )
    elif parsed.scheme != parsed.scheme.lower():
        s = parsed._replace(scheme=parsed.scheme.lower()).geturl()
        hints.append(f"Lowercased scheme {parsed.scheme!r} → {parsed.scheme.lower()!r}.")
        parsed = urlparse(s)

    # Lowercase host. URL hosts are case-insensitive; many sites 404 on
    # uppercase paths but never on uppercase hosts, so this is always
    # safe.
    host = (parsed.hostname or "").lower()
    if not host:
        raise URLValidationError(
            f"Couldn't find a host in {raw!r}. Example: https://sentinel.com"
        )
    if not _HOST_PATTERN.match(host):
        raise URLValidationError(
            f"{host!r} doesn't look like a valid hostname (need at "
            f"least one dot, e.g. sentinel.com). Example: https://sentinel.com"
        )

    # Keep port if present, drop everything below the host (path / query
    # / fragment). Build entry is per-company, not per-page.
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"

    if parsed.path and parsed.path not in ("", "/"):
        hints.append(f"Removed path {parsed.path!r} — research uses the company root.")
    if parsed.query:
        hints.append(f"Removed query string ?{parsed.query} — not needed for research.")
    if parsed.fragment:
        hints.append(f"Removed fragment #{parsed.fragment}.")

    canonical = urlunparse((parsed.scheme.lower(), netloc, "", "", "", ""))

    if canonical != raw.strip():
        # Caller decides whether to surface hints; we always include
        # the catch-all "rewrote your URL" so the UI can show a small
        # "we used this" line.
        if not hints:
            hints.append(f"Trimmed input — used {canonical} for research.")

    return NormalizedURL(url=canonical, original=raw, hints=hints)
