import re
from typing import Any, Dict, List, Tuple

from .groq_client import find_entities, summarize
from .regex_rules import regex_redact
from .types import VALID_ENTITY_TYPES, AuditEntry, ProcessResult

# Words too generic to redact on their own even when they show up inside a
# flagged name (e.g. "at" in "Neha Sharma at Krishnamurthy & Associates").
_NAME_STOPWORDS = {"the", "and", "of", "de", "van", "der", "la", "el", "al", "at", "for"}

_COMPANY_SUFFIX_RE = re.compile(
    r"\s*,?\s*(?:Pvt\.?\s*Ltd\.?|Ltd\.?|Inc\.?|LLC|LLP|Corp\.?|Co\.?|Limited|Incorporated|Company)\s*\.?\s*$",
    re.IGNORECASE,
)

# Candidate legal suffixes to probe for when checking whether a *longer*
# verbatim form of a flagged company/firm name exists elsewhere in the text
# (e.g. the model returns "PixelForge Studios" but the header actually reads
# "PixelForge Studios Inc.").
_COMPANY_SUFFIX_CANDIDATES = [
    "Pvt. Ltd.", "Pvt Ltd", "Ltd.", "Ltd", "Inc.", "Inc", "LLC", "LLP",
    "Corp.", "Corp", "Limited", "Incorporated", "Company", "Co.",
]


def _longest_variant_in_text(text: str, base: str) -> str:
    """If the text contains `base` immediately followed by a legal suffix
    (e.g. "PixelForge Studios" -> "PixelForge Studios Inc."), return that
    longer verbatim form so the whole thing gets redacted as one span
    instead of leaving a dangling " Inc." behind."""
    best = base
    for suffix in _COMPANY_SUFFIX_CANDIDATES:
        for sep in (" ", ", "):
            candidate = f"{base}{sep}{suffix}"
            if len(candidate) > len(best) and candidate in text:
                best = candidate
    return best


def _redaction_fragments(entity_type: str, original_text: str) -> List[str]:
    """For PERSON_NAME / LEGAL_COUNSEL, also derive individual name parts
    (first/last name); for COMPANY_NAME / LEGAL_COUNSEL, also derive the
    company name with its legal suffix stripped. These catch a later bare
    mention — e.g. just "Rajesh" or "NovaTech Solutions" without "Ltd." —
    that the model's exact span wouldn't match on its own."""
    fragments: List[str] = []

    if entity_type in ("PERSON_NAME", "LEGAL_COUNSEL"):
        for part in re.findall(r"[A-Za-z']+", original_text):
            if len(part) >= 3 and part.lower() not in _NAME_STOPWORDS:
                fragments.append(part)

    if entity_type in ("COMPANY_NAME", "LEGAL_COUNSEL"):
        stripped = _COMPANY_SUFFIX_RE.sub("", original_text).strip()
        if stripped and stripped.lower() != original_text.lower() and len(stripped) >= 3:
            fragments.append(stripped)

    return fragments


def _redact_fragment(text: str, fragment: str, placeholder: str) -> str:
    pattern = re.compile(r"\b" + re.escape(fragment) + r"\b")
    return pattern.sub(placeholder, text)


def _apply_llm_entities(text: str, entities: List[Dict[str, Any]]) -> Tuple[str, List[AuditEntry]]:
    """Take the LLM's flagged entities and apply them to the transcript via
    exact-substring find-and-replace — the LLM never touches the transcript
    text itself, so everything not explicitly redacted is guaranteed
    byte-identical to the input.

    This runs in two phases, deliberately kept separate:

    Phase 1 — full-entity matches only. Longest spans are applied first so a
    full "Name at Law Firm" match isn't partially clobbered by a shorter
    contained "Name" match. Before giving up on an exact match, we also check
    for a longer verbatim variant with a legal suffix appended (e.g. the
    model returns "PixelForge Studios" but the transcript header actually
    says "PixelForge Studios Inc.") so the whole thing is redacted as one
    span instead of leaving a dangling " Inc." behind. If nothing matches
    verbatim (the model paraphrased instead of copying), the entity is NOT
    silently dropped — it's kept in the audit log with status="not_found".

    Phase 2 — fragment-level redaction (name parts, company name without its
    legal suffix), applied only after every full entity from phase 1 has
    already been redacted. This ordering matters: if fragment matching ran
    interleaved with phase 1 (per-entity, immediately), a fragment from one
    entity (e.g. "Sharma" from "Neha Sharma at Krishnamurthy & Associates")
    could bleed into a completely different entity's still-unredacted span
    (e.g. a hypothetical "Sharma Textiles Ltd."). Running phase 2 only after
    all phase-1 replacements are locked in means any other entity's full
    span is already gone from the literal text (replaced by its own
    placeholder) by the time fragments are considered — a fragment simply
    can't match inside text that's no longer there. As an extra safeguard,
    each fragment is also skipped if it's a substring of any *other*
    entity's original text, whether or not that other entity was
    successfully redacted.
    """
    audit_entries: List[AuditEntry] = []

    sorted_entities = sorted(
        entities, key=lambda e: len(str(e.get("originalText", ""))), reverse=True
    )

    all_original_texts = [
        str(e.get("originalText", "")).strip()
        for e in sorted_entities
        if str(e.get("originalText", "")).strip()
    ]

    # (entity_type, original_text, placeholder) for entities actually
    # redacted in phase 1 — only these feed into phase 2.
    applied_entities: List[Tuple[str, str, str]] = []

    # --- Phase 1: full-entity matches only ---
    for entity in sorted_entities:
        original = str(entity.get("originalText", "")).strip()
        entity_type = entity.get("type")
        reason = str(entity.get("reason", ""))
        try:
            confidence = float(entity.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        if not original or entity_type not in VALID_ENTITY_TYPES:
            continue  # malformed entry from the model — nothing usable to show

        placeholder = f"[{entity_type}]"

        # A confidence of 0 is the model explicitly saying "not confidential,
        # don't redact this" (e.g. the analyst's own firm) — honor that by
        # skipping the replacement entirely rather than redacting it anyway.
        # The prompt now instructs the model not to return these at all, but
        # this is a defensive backstop in case it still does.
        if confidence <= 0.0:
            audit_entries.append(
                AuditEntry(
                    type=entity_type,
                    original_text=original,
                    placeholder=placeholder,
                    reason=reason,
                    confidence=confidence,
                    detected_by="llm",
                    status="excluded",
                )
            )
            continue

        applied = False

        if entity_type in ("COMPANY_NAME", "LEGAL_COUNSEL"):
            expanded = _longest_variant_in_text(text, original)
            if expanded != original and expanded in text:
                text = text.replace(expanded, placeholder)
                applied = True

        if original in text:
            text = text.replace(original, placeholder)
            applied = True

        if not applied:
            audit_entries.append(
                AuditEntry(
                    type=entity_type,
                    original_text=original,
                    placeholder=placeholder,
                    reason=reason,
                    confidence=confidence,
                    detected_by="llm",
                    status="not_found",
                )
            )
            continue

        audit_entries.append(
            AuditEntry(
                type=entity_type,
                original_text=original,
                placeholder=placeholder,
                reason=reason,
                confidence=confidence,
                detected_by="llm",
                status="redacted",
            )
        )
        applied_entities.append((entity_type, original, placeholder))

    # --- Phase 2: fragment-level redaction, only after every full entity
    # above has already been applied ---
    for entity_type, original, placeholder in applied_entities:
        other_texts = [t for t in all_original_texts if t != original]
        for fragment in _redaction_fragments(entity_type, original):
            if any(fragment in other for other in other_texts):
                continue  # could belong to a different flagged entity — skip
            text = _redact_fragment(text, fragment, placeholder)

    return text, audit_entries


def process_transcript(raw_text: str) -> ProcessResult:
    # Layer 1: deterministic regex (emails, phones) — a safety net that
    # doesn't depend on the model being available or correct.
    partially_redacted, regex_entries = regex_redact(raw_text)

    # Layer 2: LLM finds contextual entities (names, companies, addresses,
    # financial figures, legal counsel). It returns entities only, never a
    # rewritten transcript — the redaction itself happens locally below.
    entities = find_entities(partially_redacted)
    redacted_text, llm_entries = _apply_llm_entities(partially_redacted, entities)

    # Summarization sees ONLY the fully redacted text — the raw transcript
    # is never included in this prompt, so PII cannot reach the summary.
    summary_result = summarize(redacted_text)

    return ProcessResult(
        redacted_text=redacted_text,
        summary=str(summary_result.get("summary", "")),
        key_points=[str(point) for point in summary_result.get("keyPoints", [])],
        audit_log=regex_entries + llm_entries,
    )
