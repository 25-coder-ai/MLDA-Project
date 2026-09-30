"""Claim-blind sealed evidence ledger.

Every ground-truth artifact is appended to a SHA-256 hash chain *before* any AI claim
is parsed; the chain is then sealed. Verification entries appended after the seal
reference evidence that provably pre-dates the claims, so evidence cannot have been
shaped by (or cherry-picked for) the assertions it later judges. The chain is
re-verifiable by any third party from the exported entries alone.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, List, Optional

from src.models.evidence import LedgerEntry, LedgerModel

GENESIS = "0" * 64


def canonical_digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def link_hash(seq: int, stage: str, kind: str, source: str, digest: str, prev: str) -> str:
    return hashlib.sha256(f"{seq}|{stage}|{kind}|{source}|{digest}|{prev}".encode("utf-8")).hexdigest()


class EvidenceLedger:
    def __init__(self) -> None:
        self.entries: List[LedgerEntry] = []
        self.seal_seq: Optional[int] = None

    @property
    def head(self) -> str:
        return self.entries[-1].hash if self.entries else GENESIS

    @property
    def sealed(self) -> bool:
        return self.seal_seq is not None

    def append(self, stage: str, kind: str, source: str, payload: Any, summary: str) -> LedgerEntry:
        seq = len(self.entries)
        digest = canonical_digest(payload)
        prev = self.head
        entry = LedgerEntry(seq=seq, stage=stage, kind=kind, source=source, summary=summary, digest=digest, prev_hash=prev, hash=link_hash(seq, stage, kind, source, digest, prev), timestamp=round(time.time(), 3))
        self.entries.append(entry)
        return entry

    def seal(self) -> LedgerEntry:
        if self.sealed:
            raise RuntimeError("ledger already sealed")
        entry = self.append("seal", "SEAL", "trustcode", {"evidence_entries": len(self.entries), "head": self.head}, f"Ground truth sealed over {len(self.entries)} evidence entries before any AI claim was read")
        self.seal_seq = entry.seq
        return entry

    def verify(self) -> bool:
        prev = GENESIS
        for i, e in enumerate(self.entries):
            if e.seq != i or e.prev_hash != prev or e.hash != link_hash(e.seq, e.stage, e.kind, e.source, e.digest, e.prev_hash):
                return False
            prev = e.hash
        return True

    def model(self) -> LedgerModel:
        seal = self.entries[self.seal_seq].hash if self.seal_seq is not None else None
        return LedgerModel(entries=list(self.entries), seal_seq=self.seal_seq, seal_hash=seal, head_hash=self.head, intact=self.verify())
