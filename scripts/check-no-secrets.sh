#!/usr/bin/env bash
# Cheap pre-commit insurance: grep the tree for token-shaped strings
# before they hit a public remote. Not a replacement for `gitleaks` or
# `trufflehog`, but catches the obvious mistakes (a stray `sk-ant-...`
# in a script comment, a `glc_...` in a YAML example, a Bearer token
# pasted into a markdown snippet).
#
# Exits non-zero on any hit so it can gate CI or a pre-commit hook.
# Skips the usual noise (.venv, node_modules, dist, .env, generated data).
#
# Run from the repo root:
#   ./scripts/check-no-secrets.sh

set -euo pipefail

cd "$(dirname "$0")/.."

# Patterns to flag. Each is broad enough to catch the common formats
# but tight enough to avoid matching arbitrary alphanumeric text.
PATTERNS=(
  # Anthropic API keys
  'sk-ant-[A-Za-z0-9_-]{20,}'
  # Grafana Cloud access policy tokens / glsa_/glc_/glab_ patterns
  'gl[abcs]_[A-Za-z0-9_-]{20,}'
  'glsa_[A-Za-z0-9_-]{20,}'
  # Generic JWT-ish bearer tokens
  'Bearer ey[A-Za-z0-9._-]{40,}'
  # Generic base64-padded creds longer than 40 chars after `Basic `
  'Basic [A-Za-z0-9+/]{40,}={0,2}'
  # OpenAI keys (for completeness — Clarion doesn't use them today but
  # if someone copies code around it's nice to catch)
  'sk-[A-Za-z0-9]{40,}'
  # AWS access keys
  'AKIA[0-9A-Z]{16}'
)

# Files we deliberately don't scan:
#   .git           — internal git state
#   .venv          — Python virtualenv (not committed; many fixtures inside)
#   node_modules   — npm deps
#   dist / build   — compiled output
#   .env*          — local secrets (gitignored anyway)
#   data/{profiles,plans,generated} — runtime artifacts (gitignored)
#   .claude        — claude-code local state
#   scripts/check-no-secrets.sh — this file lists the patterns!
EXCLUDES=(
  --exclude-dir=.git
  --exclude-dir=.venv
  --exclude-dir=node_modules
  --exclude-dir=dist
  --exclude-dir=build
  --exclude-dir=.claude
  --exclude-dir=.pytest_cache
  --exclude-dir=.mypy_cache
  --exclude-dir=.ruff_cache
  --exclude=.env
  --exclude=.env.*
  --exclude=check-no-secrets.sh
)

# Also skip the generated-data dirs (rooted, not just by name).
SKIP_DIRS=(
  data/profiles
  data/plans
  data/generated
  data/cache
)

# Build the prune args. We pass them to find which feeds grep -P.
PRUNE_ARGS=()
for d in "${SKIP_DIRS[@]}"; do
  if [[ -d "$d" ]]; then
    PRUNE_ARGS+=(-path "./$d" -prune -o)
  fi
done

hits=0
for pat in "${PATTERNS[@]}"; do
  # `-rE` for extended regex, `-l` to just list files (avoid printing
  # the actual matched secret), then re-grep with a redacted output.
  matches=$(grep -rE "${EXCLUDES[@]}" -l -- "$pat" . 2>/dev/null || true)
  if [[ -n "$matches" ]]; then
    echo "✗ potential secret matching /$pat/:"
    while IFS= read -r f; do
      echo "    $f"
    done <<< "$matches"
    hits=$((hits + 1))
  fi
done

if [[ "$hits" -gt 0 ]]; then
  echo ""
  echo "Found $hits pattern(s) that look like leaked secrets."
  echo "If these are false positives, add a comment-marker your future"
  echo "self will recognise (e.g. '# example-only') and revise the regex"
  echo "in this script to exclude that pattern."
  exit 1
fi

echo "✓ No secret-shaped strings found in tracked / committable files."
