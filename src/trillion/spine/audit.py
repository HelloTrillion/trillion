"""SPN-12: what the audit log records. Argument hashes only, never argument values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class AuditRecord:
    owner_id: str
    call_id: str
    tool: str
    args_hash: str
    risk: str
    decision: str
    outcome: str
    duration_ms: int
    session_id: str
    at: int
