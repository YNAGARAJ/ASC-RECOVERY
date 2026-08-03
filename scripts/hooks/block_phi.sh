#!/usr/bin/env bash
# PreToolUse hook — blocks writes that look like they contain real PHI or
# secrets. Reads the tool call as JSON on stdin. Exit 2 blocks the tool call
# (message goes to stderr); exit 0 allows it.
set -u

INPUT="$(cat)"

PY=""
command -v python3 >/dev/null 2>&1 && PY=python3
[ -z "$PY" ] && command -v python >/dev/null 2>&1 && PY=python

if command -v jq >/dev/null 2>&1; then
  FILE_PATH="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
  CONTENT="$(printf '%s' "$INPUT" | jq -r '
    [.tool_input.content, .tool_input.new_string] | map(select(. != null)) | join("\n")
  ' 2>/dev/null)"
elif [ -n "$PY" ]; then
  # jq isn't installed. Parse the JSON properly (stdlib json, no deps) rather
  # than regex-scanning the raw blob: raw JSON escapes a newline as a literal
  # backslash character followed by the letter that normally follows a
  # backslash in a C-style escape, so a word ending right before an escaped
  # line break can read, to a naive scanner, as if that trailing letter were
  # glued onto whatever text starts the next line -- producing spurious
  # matches for the email/phone/key patterns below. A real JSON parse avoids
  # that whole class of false positive.
  FILE_PATH="$(printf '%s' "$INPUT" | "$PY" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
print(data.get("tool_input", {}).get("file_path", "") or "")
' 2>/dev/null)"
  CONTENT="$(printf '%s' "$INPUT" | "$PY" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
ti = data.get("tool_input", {})
parts = [p for p in (ti.get("content"), ti.get("new_string")) if p is not None]
sys.stdout.write("\n".join(parts))
' 2>/dev/null)"
else
  # Last-resort fallback without jq or python: operate on the raw JSON blob.
  # Less precise (may scan field names/escape sequences too) but still catches
  # the same patterns.
  FILE_PATH="$(printf '%s' "$INPUT" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')"
  CONTENT="$INPUT"
fi

fail() {
  echo "BLOCKED by block_phi.sh: $1" >&2
  exit 2
}

# --- Path checks -----------------------------------------------------------
if [ -n "${FILE_PATH:-}" ]; then
  case "$FILE_PATH" in
    *data/real/*) fail "write targets data/real/ — no real PHI in this repo." ;;
    */phi/*|phi/*) fail "write targets a phi/ path — no real PHI in this repo." ;;
    */secrets/*|secrets/*) fail "write targets a secrets/ path." ;;
    *.pem) fail "write targets a .pem file (private key material)." ;;
    *.key) fail "write targets a .key file (key material)." ;;
  esac
fi

# --- Content checks ----------------------------------------------------------
if [ -n "${CONTENT:-}" ]; then
  # US SSN pattern: NNN-NN-NNNN
  if printf '%s' "$CONTENT" | grep -qE '\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b'; then
    fail "content matches a US SSN pattern."
  fi

  # Medicare Beneficiary Identifier: 11-char alphanumeric, dashes at 4/8
  # e.g. 1EG4-TE5-MK73
  if printf '%s' "$CONTENT" | grep -qE '\b[0-9][A-Za-z][A-Za-z0-9][0-9]-[A-Za-z][A-Za-z0-9][0-9]-[A-Za-z0-9]{2}[A-Za-z][0-9]\b'; then
    fail "content matches a Medicare Beneficiary Identifier (MBI) pattern."
  fi

  # Real-looking email, allowing @example.com, @test, @localhost
  EMAILS="$(printf '%s' "$CONTENT" | grep -oE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9._%+-]+@(test|localhost)')"
  if [ -n "$EMAILS" ]; then
    BAD_EMAILS="$(printf '%s\n' "$EMAILS" | grep -viE '@(example\.com|test|localhost)$')"
    if [ -n "$BAD_EMAILS" ]; then
      fail "content contains a real-looking email address (only @example.com, @test, @localhost allowed): $(printf '%s' "$BAD_EMAILS" | head -n1)"
    fi
  fi

  # Real-looking US phone number, allowing the reserved 555-01xx range
  PHONES="$(printf '%s' "$CONTENT" | grep -oE '(\([0-9]{3}\)[ .-]?|[0-9]{3}[.-])[0-9]{3}[.-][0-9]{4}')"
  if [ -n "$PHONES" ]; then
    BAD_PHONES="$(printf '%s\n' "$PHONES" | grep -viE '555[.-]01[0-9]{2}$')"
    if [ -n "$BAD_PHONES" ]; then
      fail "content contains a real-looking US phone number (only 555-01xx allowed): $(printf '%s' "$BAD_PHONES" | head -n1)"
    fi
  fi

  # AWS access key
  if printf '%s' "$CONTENT" | grep -qE '\bAKIA[0-9A-Z]{16}\b'; then
    fail "content contains what looks like an AWS access key."
  fi

  # OpenAI API key
  if printf '%s' "$CONTENT" | grep -qE '\bsk-[A-Za-z0-9]{20,}\b'; then
    fail "content contains what looks like an OpenAI API key."
  fi

  # Private key block
  if printf '%s' "$CONTENT" | grep -qE -- '-----BEGIN [A-Z ]*PRIVATE KEY-----'; then
    fail "content contains a private key block."
  fi

  # GitHub token
  if printf '%s' "$CONTENT" | grep -qE '\bgh[pousr]_[A-Za-z0-9]{36,}\b'; then
    fail "content contains what looks like a GitHub token."
  fi

  # Explicit phrases that indicate someone is about to commit real PHI
  if printf '%s' "$CONTENT" | grep -qiE 'real patient|production phi'; then
    fail "content contains the phrase \"real patient\" or \"production phi\"."
  fi
fi

exit 0
