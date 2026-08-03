"""The variance-detection rules version stamped onto every finding at save
time, so results stay reproducible after domain.variance changes -- per the
phase's `rule_version` requirement. Bump this whenever a classification
rule in domain/variance.py changes in a way that could alter past output.
"""

from __future__ import annotations

RULES_VERSION = "2026.08.0"
