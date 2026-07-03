import re
from typing import List, Tuple

from .types import AuditEntry

# Deterministic, unambiguous PII patterns only. Everything that needs
# contextual judgment (names, company confidentiality, addresses, financial
# figures, legal counsel) is handled by the LLM layer in groq_client.py /
# pipeline.py — this module is a safety net that doesn't depend on the model
# being available or correct.

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"\+\d{1,3}[\s.-]?\d{4,5}[\s.-]?\d{4,6}")


def regex_redact(text: str) -> Tuple[str, List[AuditEntry]]:
    entries: List[AuditEntry] = []

    def replace_email(match: "re.Match[str]") -> str:
        entries.append(
            AuditEntry(
                type="EMAIL",
                original_text=match.group(0),
                placeholder="[EMAIL]",
                reason="Matched email address pattern (user@domain.tld).",
                confidence=0.99,
                detected_by="regex",
                status="redacted",
            )
        )
        return "[EMAIL]"

    def replace_phone(match: "re.Match[str]") -> str:
        entries.append(
            AuditEntry(
                type="PHONE_NUMBER",
                original_text=match.group(0),
                placeholder="[PHONE_NUMBER]",
                reason="Matched international phone number pattern (+country code).",
                confidence=0.95,
                detected_by="regex",
                status="redacted",
            )
        )
        return "[PHONE_NUMBER]"

    text = EMAIL_RE.sub(replace_email, text)
    text = PHONE_RE.sub(replace_phone, text)

    return text, entries
