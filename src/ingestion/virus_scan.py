"""Virus scanning port for ingested files.

Same shape as security/kms.py's port + adapter pattern: a `Protocol` so the
pipeline works unchanged against any real engine (ClamAV, a cloud AV
service), with a dev/test adapter that doesn't need one installed. Real
malware-engine integration is out of scope here -- deferred the same way
Phase 4 deferred real cloud KMS adapters to Phase 9.

`EicarAwareScanner` detects the EICAR test string, the industry-standard
antivirus test signature (not a real virus -- see
https://www.eicar.org/download-anti-malware-testfile/), so the quarantine
path can be proven to actually engage without a real AV engine or a real
malicious payload anywhere in this repo (CLAUDE.md rule 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

EICAR_TEST_STRING = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
).encode("ascii")


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    detail: str


class VirusScanner(Protocol):
    def scan(self, content: bytes) -> ScanResult: ...


class EicarAwareScanner(VirusScanner):
    """Not a substitute for a real antivirus engine. Flags only the EICAR
    test string; everything else is reported clean."""

    def scan(self, content: bytes) -> ScanResult:
        if EICAR_TEST_STRING in content:
            return ScanResult(clean=False, detail="EICAR test signature detected")
        return ScanResult(clean=True, detail="no known signature detected")
