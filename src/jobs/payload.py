"""Pure serialization for job payloads -- Phase 7
(`docs/MASTER-BUILD-PROMPT-V2.md`). Encryption itself happens at the
call site (`api/repository.py` on enqueue, `src/jobs/runner.py` on
execution) via the existing `security.phi_columns.encrypt_phi_field`/
`decrypt_phi_field` -- this module only turns an ingestion job's raw
file bytes + metadata into (and back out of) the plain string those
functions encrypt/decrypt, so it's fully unit-testable without any
crypto or database in the loop.
"""

from __future__ import annotations

import base64
import json


def build_ingestion_payload(content: bytes, *, source: str) -> str:
    """JSON has no native bytes type, hence base64 -- `source` travels
    alongside since `ingestion.pipeline.ingest_file` needs it and there's
    nowhere else on the job row to put it without leaking it into an
    unencrypted column for no reason."""
    return json.dumps({"content_b64": base64.b64encode(content).decode("ascii"), "source": source})


def parse_ingestion_payload(serialized: str) -> tuple[bytes, str]:
    data = json.loads(serialized)
    return base64.b64decode(data["content_b64"]), data["source"]
