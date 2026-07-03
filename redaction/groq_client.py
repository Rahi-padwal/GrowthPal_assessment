import json
import os
from typing import Any, Dict, List

from groq import Groq


class RateLimitedError(Exception):
    """Raised when Groq returns a 429 — surfaced to the user as a clear,
    non-crashing message instead of a stack trace."""


class AIProcessingError(Exception):
    """Raised for any other Groq failure (bad key, network error, malformed
    response, etc.)."""


MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise AIProcessingError(
                "GROQ_API_KEY is not set. Add it to your environment before starting the server."
            )
        _client = Groq(api_key=api_key)
    return _client


ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_entities",
        "description": (
            "Return every sensitive entity found in the transcript that must be "
            "redacted. Do not return the transcript itself — only the list of "
            "entities. The calling application will find-and-replace each one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "PERSON_NAME",
                                    "COMPANY_NAME",
                                    "ADDRESS",
                                    "FINANCIAL_FIGURE",
                                    "LEGAL_COUNSEL",
                                ],
                            },
                            "originalText": {
                                "type": "string",
                                "description": (
                                    "The exact substring from the transcript to redact, "
                                    "copied verbatim — same casing, punctuation, and "
                                    "whitespace as it appears in the transcript. This is "
                                    "used for exact string matching, not fuzzy matching."
                                ),
                            },
                            "reason": {
                                "type": "string",
                                "description": "One sentence explaining why this was flagged.",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "0 to 1.",
                            },
                        },
                        "required": ["type", "originalText", "reason", "confidence"],
                    },
                },
            },
            "required": ["entities"],
        },
    },
}

SUMMARY_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_summary",
        "description": "Return the call summary and key points.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "150-250 word neutral summary of the call.",
                },
                "keyPoints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "5 to 10 concise bullet points of concrete facts discussed.",
                },
            },
            "required": ["summary", "keyPoints"],
        },
    },
}

SYSTEM_PROMPT_ENTITIES = """You are a PII and confidential-information detector for an investment \
analyst call transcript. The transcript may already contain placeholders like [EMAIL] or \
[PHONE_NUMBER] — ignore those, they are already handled.

Find every span of text that must be redacted and classify it as one of:
- PERSON_NAME: any individual's name (analyst, interviewee, third parties mentioned) — \
including names that appear inside speaker labels like "CFO (Name):".
- COMPANY_NAME: company/organization names that are confidential or third-party — the \
portfolio company being discussed, its clients, vendors, competitors, and any other named \
business.
- ADDRESS: physical/mailing addresses (street, building, plot, sector, postal code). Do NOT \
flag bare city or region names used generically (e.g. "our offices in Mumbai, Pune, and \
Bengaluru") — only flag a specific street-level address.
- FINANCIAL_FIGURE: non-public financial figures — revenue, margins, burn rate, ARR, \
valuation, monetary amounts, and percentages/durations that are themselves reporting a \
financial metric. Do NOT flag generic non-financial numbers (headcount counts, "17 new \
logos", a bare growth adjective without a specific tied figure) unless the number itself is \
a financial disclosure.
- LEGAL_COUNSEL: names of lawyers and law firms. If a person or firm is legal counsel, use \
this type instead of PERSON_NAME/COMPANY_NAME.

Critical exception — the calling analyst's own firm:
Every transcript names the analyst's own firm, usually in the header ("Call recording — Firm \
A × Firm B") and/or when the analyst introduces themselves ("calling from Firm A"). Firm A in \
that pattern is the analyst's own employer, not a confidential third party, and must NEVER \
appear anywhere in your output — not as a COMPANY_NAME entity, not with low confidence, not \
with a note explaining why it's excluded. Simply act as if that firm's name does not exist as \
a candidate at all. Every other company name (the subject/portfolio company, clients, \
vendors, competitors, law firms) is still redacted normally.

Rules:
- Each "originalText" value must be an exact, verbatim substring of the transcript — same \
casing, spacing, and punctuation — since it will be used for exact string matching.
- Return each distinct span once (do not repeat the same span for every occurrence in the \
transcript).
- Do not over-redact: leave non-sensitive content (generic city names, generic role titles, \
non-financial counts) out of the list entirely.
- Only include entities you are actually flagging for redaction. Never include an entity just \
to explain that it should NOT be redacted (e.g. never return the analyst's own firm with \
confidence 0 as a way of noting it's excluded) — if something shouldn't be redacted, leave it \
out of the entities array entirely.
- Call the submit_entities tool with your result. Do not respond in plain text."""

SYSTEM_PROMPT_SUMMARY = """You are summarizing an already-redacted investment analyst call \
transcript. All PII and confidential figures have been replaced with bracketed placeholders \
like [PERSON_NAME], [COMPANY_NAME], [FINANCIAL_FIGURE] — you are only ever shown the redacted \
version, never the original.

Write a concise 150-250 word summary of the call using only what is stated in the transcript \
— do not infer or invent details, and do not alter the meaning of anything said. Refer to \
participants by their role (Analyst, CFO, etc.), never by name.

Also extract 5 to 10 key points as a structured bullet list covering the concrete facts \
discussed, using the placeholders as they appear (e.g. "[FINANCIAL_FIGURE] EBITDA margin \
reported").

Only report what was actually said. Never mention topics that were NOT discussed, figures \
that were NOT reported, or questions that went unanswered — do not write things like "EBITDA \
margin was not reported" or "no update was given on X". If something wasn't covered in the \
transcript, simply leave it out of the summary and key points entirely rather than noting its \
absence.

Call the submit_summary tool with your result. Do not respond in plain text."""


def _is_rate_limit_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    return "rate limit" in str(exc).lower() or "rate_limit" in str(exc).lower()


def _call_tool(system_prompt: str, user_content: str, tool: Dict[str, Any], tool_name: str) -> Dict[str, Any]:
    client = _get_client()

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any Groq failure must degrade gracefully
        if _is_rate_limit_error(exc):
            raise RateLimitedError(
                "The AI service is temporarily rate-limited. Please wait a moment and try again."
            ) from exc
        raise AIProcessingError(
            "The AI service failed to process this transcript. Please try again."
        ) from exc

    message = response.choices[0].message
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        raise AIProcessingError("The AI service did not return a structured result. Please try again.")

    try:
        return json.loads(tool_calls[0].function.arguments)
    except (json.JSONDecodeError, AttributeError, IndexError) as exc:
        raise AIProcessingError("The AI service returned an unreadable result. Please try again.") from exc


def find_entities(partially_redacted_text: str) -> List[Dict[str, Any]]:
    result = _call_tool(SYSTEM_PROMPT_ENTITIES, partially_redacted_text, ENTITIES_TOOL, "submit_entities")
    return result.get("entities", [])


def summarize(redacted_text: str) -> Dict[str, Any]:
    return _call_tool(SYSTEM_PROMPT_SUMMARY, redacted_text, SUMMARY_TOOL, "submit_summary")
