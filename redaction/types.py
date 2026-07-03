from dataclasses import dataclass
from typing import List, Literal

EntityType = Literal[
    "PERSON_NAME",
    "COMPANY_NAME",
    "ADDRESS",
    "FINANCIAL_FIGURE",
    "LEGAL_COUNSEL",
    "EMAIL",
    "PHONE_NUMBER",
]

DetectedBy = Literal["regex", "llm"]
EntryStatus = Literal["redacted", "not_found", "excluded"]

VALID_ENTITY_TYPES = {
    "PERSON_NAME",
    "COMPANY_NAME",
    "ADDRESS",
    "FINANCIAL_FIGURE",
    "LEGAL_COUNSEL",
}


@dataclass
class AuditEntry:
    """One flagged entity: what it was, why it was flagged, and whether the
    redaction actually applied to the transcript text."""

    type: EntityType
    original_text: str
    placeholder: str
    reason: str
    confidence: float
    detected_by: DetectedBy
    status: EntryStatus = "redacted"


@dataclass
class ProcessResult:
    redacted_text: str
    summary: str
    key_points: List[str]
    audit_log: List[AuditEntry]
