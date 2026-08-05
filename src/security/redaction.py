"""PHI redaction for logs, traces, and exceptions -- per CLAUDE.md rule 6
("no PHI in logs, traces, error messages, or URLs. Assume every log line
will be read by someone unauthorized") and the phase prompt.

Two layers:

1. **Structured fields** (the reliable mechanism): if a `LogRecord` carries
   `extra` keys in `PHI_FIELD_NAMES` (patient_name, dob, mrn, ssn,
   member_id, address, ...), their values are replaced with
   `"[REDACTED]"` before formatting. Callers should pass any PHI-adjacent
   value via `extra={...}` rather than interpolating it into the message
   string -- that's what makes it catchable at all.
2. **Free-text scrubbing** (defense in depth, not a substitute for #1):
   the rendered message and exception text are also regex-scrubbed for
   SSN- and MBI-shaped patterns, transcribed 1:1 from
   scripts/hooks/block_phi.sh (bash/grep and Python can't literally share
   source across languages). This only catches PHI that happens to match a
   recognizable structural pattern -- it will not catch an arbitrary
   patient name typed directly into an f-string message. There is no
   general-purpose way to do that reliably; passing PHI via `extra=`
   fields is the only mechanism this module can actually guarantee.

Attach `PHIRedactionFilter` to the logger (not just a handler) so it runs
before the record is dispatched anywhere, per "redact at the source, not
the sink."
"""

from __future__ import annotations

import logging
import re

_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_MBI_PATTERN = re.compile(
    r"\b[0-9][A-Za-z][A-Za-z0-9][0-9]-[A-Za-z][A-Za-z0-9][0-9]-[A-Za-z0-9]{2}[A-Za-z][0-9]\b"
)

_REDACTED = "[REDACTED]"

PHI_FIELD_NAMES = frozenset(
    {
        "patient_name",
        "first_name",
        "last_name",
        "dob",
        "date_of_birth",
        "mrn",
        "ssn",
        "member_id",
        "patient_member_id",
        "address",
        "phone",
        "email",
    }
)

_exception_formatter = logging.Formatter()


class PHIRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for field_name in PHI_FIELD_NAMES:
            if hasattr(record, field_name):
                setattr(record, field_name, _REDACTED)

        record.msg = scrub_text(str(record.msg))
        if record.args:
            record.args = tuple(
                scrub_text(arg) if isinstance(arg, str) else arg for arg in record.args
            )

        if record.exc_info and not record.exc_text:
            # Pre-computing and scrubbing exc_text here means the real
            # Formatter, which runs after filters, sees it already set and
            # uses this scrubbed version verbatim instead of recomputing
            # the raw traceback text itself.
            record.exc_text = scrub_text(_exception_formatter.formatException(record.exc_info))

        return True


def scrub_text(text: str) -> str:
    """Regex-scrub SSN/MBI-shaped substrings -- the same defense-in-depth
    mechanism `PHIRedactionFilter` applies to log records, exposed
    publicly so `observability.tracing`'s span-attribute scrubbing reuses
    these exact patterns instead of a second, driftable copy of them."""
    text = _SSN_PATTERN.sub(_REDACTED, text)
    text = _MBI_PATTERN.sub(_REDACTED, text)
    return text
