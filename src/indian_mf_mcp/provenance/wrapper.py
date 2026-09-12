"""Two-tier provenance envelope builder: facts reference a shared `sources` table by key
and carry only an epistemic tag, keeping payloads legible (spec §6)."""
from __future__ import annotations

from dataclasses import dataclass, field

from indian_mf_mcp.provenance.epistemic import validate


@dataclass
class ProvenanceBuilder:
    data: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    _next_src_id: int = 1
    _next_calc_id: int = 1

    def add_source(self, **meta) -> str:
        key = f"s{self._next_src_id}"
        self._next_src_id += 1
        self.sources[key] = meta
        return key

    def add_calc(self, **meta) -> str:
        key = f"c{self._next_calc_id}"
        self._next_calc_id += 1
        self.sources[key] = {"type": "calculated", **meta}
        return key

    def fact(self, name: str, value, src: str, kind: str, caveat: str | None = None):
        validate(kind)
        entry = {"v": value, "src": src, "k": kind}
        if caveat:
            entry["caveat"] = caveat
        self.data[name] = entry

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def build(self, provenance: str = "compact", extra_meta: dict | None = None) -> dict:
        out = {"data": self.data, "sources": self.sources if provenance != "none" else {}}
        meta = dict(extra_meta or {})
        if self.warnings:
            meta["warnings"] = self.warnings
        out["meta"] = meta
        return out
