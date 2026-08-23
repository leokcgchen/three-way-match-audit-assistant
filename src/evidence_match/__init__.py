"""证据匹配模块对外导出。"""

from src.evidence_match.linker import (
    ROLE_LABELS,
    EvidenceLink,
    EvidenceMatchResult,
    EvidenceNode,
    build_evidence_chain,
)

__all__ = [
    "ROLE_LABELS",
    "EvidenceNode",
    "EvidenceLink",
    "EvidenceMatchResult",
    "build_evidence_chain",
]
