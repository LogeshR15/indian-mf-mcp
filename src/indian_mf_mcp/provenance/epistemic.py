"""The five epistemic kinds a fact can carry. Every emitted fact must use one of these."""
from __future__ import annotations

OFFICIAL = "official"
CALCULATED = "calculated"
OBSERVED = "observed"
APPROXIMATION = "approximation"
INFERRED = "inferred"

VALID_KINDS = {OFFICIAL, CALCULATED, OBSERVED, APPROXIMATION, INFERRED}


def validate(kind: str) -> str:
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid epistemic kind: {kind!r}, must be one of {VALID_KINDS}")
    return kind
