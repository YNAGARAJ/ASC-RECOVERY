#!/usr/bin/env bash
# PostToolUse hook — after a write to domain/, services/, or pricing/, checks
# the resulting file for float-based money handling. Reads the tool call as
# JSON on stdin. Exit 2 fails the check (message on stderr); exit 0 passes.
set -u

INPUT="$(cat)"

if command -v jq >/dev/null 2>&1; then
  FILE_PATH="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
else
  FILE_PATH="$(printf '%s' "$INPUT" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')"
fi

# Only applies to files under domain/, services/, or pricing/
case "${FILE_PATH:-}" in
  */domain/*|domain/*|*/services/*|services/*|*/pricing/*|pricing/*) : ;;
  *) exit 0 ;;
esac

if [ -z "${FILE_PATH:-}" ] || [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

fail() {
  echo "BLOCKED by check_money_types.sh: $1" >&2
  exit 2
}

# Strip lines explicitly opted out of this check.
FILTERED="$(grep -v '# noqa: money[[:space:]]*$' "$FILE_PATH")"

# float type annotation, e.g. "x: float" or "-> float"
LINE="$(printf '%s\n' "$FILTERED" | grep -nE '(:[[:space:]]*float\b)|(->[[:space:]]*float\b)' | head -n1)"
if [ -n "$LINE" ]; then
  fail "$FILE_PATH uses a float type annotation — money must be Decimal: $LINE"
fi

# float() cast
LINE="$(printf '%s\n' "$FILTERED" | grep -nE '\bfloat\(' | head -n1)"
if [ -n "$LINE" ]; then
  fail "$FILE_PATH calls float() — money must be Decimal: $LINE"
fi

# Bare decimal literal assignment not named ratio/rate/tolerance/threshold
# e.g. "x = 0.08" but not "mppr_rate = 0.08" or "tolerance = 0.01"
LINE="$(printf '%s\n' "$FILTERED" | grep -nE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*(:[[:space:]]*[A-Za-z_][A-Za-z0-9_.\[\]]*)?[[:space:]]*=[[:space:]]*[0-9]+\.[0-9]+[[:space:]]*(#.*)?$' \
  | grep -viE '(ratio|rate|tolerance|threshold)[[:space:]]*(:[^=]*)?=' \
  | head -n1)"
if [ -n "$LINE" ]; then
  fail "$FILE_PATH assigns a bare decimal literal to a variable not named ratio/rate/tolerance/threshold: $LINE"
fi

# round() called on a variable named like money
LINE="$(printf '%s\n' "$FILTERED" | grep -nE '\bround\([[:space:]]*[A-Za-z_][A-Za-z0-9_]*(amount|allowed|paid|charge|shortfall|total)[A-Za-z0-9_]*[[:space:]]*,' | head -n1)"
if [ -n "$LINE" ]; then
  fail "$FILE_PATH calls round() on a money-like variable — use Money/Decimal quantize instead: $LINE"
fi

exit 0
