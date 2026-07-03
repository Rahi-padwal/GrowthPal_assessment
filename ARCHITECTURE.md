# Architecture

A web portal that takes an analyst call transcript (`.txt`, `.docx`, or `.pdf`), returns a
redacted version with every sensitive entity replaced by a typed placeholder, a 150-250 word
summary, a 5-10 point key-point list, and an audit log explaining what was flagged and why.

## Tech stack and rationale

- **Backend: Python + FastAPI.** Single process, single language, serves both the API and the
  static frontend — no separate frontend build step, easy to run and deploy on a free-tier host
  (Render/Railway/Fly.io free tiers all support a plain Python web process).
- **Frontend: plain HTML/CSS/JS**, no framework. The UI is a drag-and-drop / click-to-select
  upload area plus four result panels (redacted transcript, summary, key points, audit log) —
  a framework would add build tooling for no real benefit at this scale.
- **File parsing:** `python-docx` for `.docx`, `pdfplumber` for `.pdf`, built-in decoding for
  `.txt`.
- **AI: [Groq](https://groq.com)**, using an open model (default `llama-3.3-70b-versatile`)
  through Groq's free tier. Groq was chosen over a local model (Ollama) because this app is
  meant to be deployable on a free hosting tier — running an LLM locally needs several GB of
  RAM that free-tier containers don't have, whereas Groq is just an outbound HTTPS call, so the
  deployed app stays small and fast to start. It was chosen over other hosted LLM APIs because
  its free tier is generous enough for this use case and needs no billing setup.

## Redaction strategy: hybrid (rule-based regex + LLM), never LLM-rewritten text

Two layers, in order:

**1. Regex (deterministic, always runs, doesn't depend on the model):**
Emails and phone numbers only — the two entity types that have an unambiguous, structural
pattern and don't need judgment. This is a safety net: even if the AI call fails outright,
these are still caught (though the rest of the pipeline still needs the AI call, so a total
Groq outage still returns a clear error rather than a partial result — see Guardrails below).

**2. LLM (contextual judgment):**
Groq is prompted to return **only a JSON list of entities** — `{type, originalText, reason,
confidence}` — for names, confidential company names, physical addresses, non-public financial
figures, and legal counsel. It is explicitly **never** asked to rewrite the transcript, and it
is explicitly instructed to omit the analyst's own firm from its output entirely rather than
including it with a low-confidence "don't redact this" note.

The actual redaction happens in plain Python (`redaction/pipeline.py`), in two phases:

- **Phase 1 — full-entity matches.** Each entity's `originalText` is found via exact substring
  match and replaced with its placeholder, longest spans first (so a full "Name at Law Firm"
  match is applied before a shorter contained "Name" match would otherwise interfere with it).
  Before giving up on an exact match, a longer verbatim variant with a legal suffix appended is
  also checked (e.g. the model returns "PixelForge Studios" but the transcript header says
  "PixelForge Studios Inc.") so the whole thing is redacted as one span instead of leaving a
  dangling " Inc." behind. As a defensive backstop, any entity the model returns with
  `confidence <= 0` is treated as explicitly excluded (not redacted) rather than applied — this
  is the same intent as the "don't include the analyst's firm" instruction, just enforced in
  code as well as in the prompt.
- **Phase 2 — fragment-level redaction**, run only after every phase-1 entity has already been
  applied. Name parts (first/last name) and a company name with its suffix stripped are also
  redacted wherever they appear standalone (e.g. a later bare "Rajesh" or "NovaTech Solutions"
  without "Ltd."). Running this only after phase 1 is complete — rather than interleaved,
  per-entity — matters: a fragment from one entity (e.g. "Sharma" from "Neha Sharma at
  Krishnamurthy & Associates") could otherwise bleed into a different entity's still-unredacted
  span. By the time phase 2 runs, every other entity's full span is already gone from the
  literal text (replaced by its own placeholder), so a fragment simply can't match inside text
  that's no longer there. As an extra safeguard, each fragment is also skipped if it's a
  substring of any *other* entity's original text, whether or not that other entity was
  successfully redacted.

This is a deliberate design choice: **the model never touches the transcript text itself**, so
anything not explicitly matched as an entity is guaranteed byte-identical to the input — the
"don't alter non-redacted content" guardrail is enforced structurally, not just by instruction.

If the model returns a span that isn't found verbatim in the transcript (e.g. it paraphrased
instead of copying exactly), that entity is **not** silently dropped — it's kept in the audit
log with `status: "not_found"` and shown highlighted in the UI, so a human reviewer can check
that spot manually instead of the tool quietly failing to redact something.

## Summarization

A **second**, separate Groq call generates the summary and key points. Its prompt contains
**only the fully redacted transcript** from the redaction step — the raw transcript is never
included in this request. This is a structural guarantee, not an instruction: even if the
model ignored "don't use names," there are no names in its context to use. The system prompt
also explicitly requires the summary to reflect only what was stated (no inference, no mention
of information that was *not* discussed) and to refer to speakers by role, never by name.

## Guardrail compliance

| Requirement | How it's met |
|---|---|
| Typed placeholder, never silently delete | Every match becomes `[TYPE]`; untouched text is never modified (see redaction strategy above). |
| Preserve speaker turns and order | Redaction is span-substitution over the original text — the structure (line order, "Analyst:"/"CFO (...):" labels) is never rebuilt or reordered. Names inside a speaker's parenthetical tag are redacted the same as any other occurrence, for consistency. |
| Handle errors gracefully (bad file, empty file, AI failure, rate limit) | `UnsupportedFileTypeError` / `EmptyFileError` → HTTP 400 with a clear message. Groq rate-limit (429) → HTTP 429 with "temporarily rate-limited, try again shortly." Any other Groq/model failure → HTTP 502 with a generic message. Any unexpected exception → HTTP 500, generic message. None of these expose internal details or crash the process. |
| Env vars for API keys, never commit secrets | Only `GROQ_API_KEY`, read from the environment in `redaction/groq_client.py`; `.env` is git-ignored, `.env.example` has a placeholder only. |
| Never log/persist raw transcripts | No database, no file writes — everything is processed in memory per request. No log statement anywhere includes transcript text, redacted text, or audit content. |
| Don't alter meaning of non-redacted content | The LLM never rewrites the transcript (see above) — only flagged spans change, verbatim substitution only. |
| Don't over-redact | The entity-extraction prompt explicitly instructs the model to leave generic city names, non-financial counts, and the analyst's own firm untouched; sample transcripts include several such cases (e.g. "312 FTEs", "17 new logos", "Mumbai, Pune, and Bengaluru") to verify this. |
| Never commit API keys | No key exists in source anywhere — `.env` is git-ignored. |

## Known limitations

- **LLM judgment isn't perfect.** Occasionally a generic city name or a financial figure
  adjacent to a real disclosure can be over- or under-flagged; this is a model-quality
  limitation rather than a code bug, and the "not found" audit status plus the confidence
  column are there specifically so a human reviewer can catch and correct edge cases before
  wider distribution — this tool is meant to assist a review, not replace it.
- **Two Groq calls per transcript** (entity extraction, then summarization) — this is
  intentional (see the structural PII-isolation argument above) but does mean total latency is
  roughly double a single call.
- **Free-tier rate limits.** Groq's free tier has request-per-minute limits; heavy concurrent
  usage will surface the 429 handling described above rather than failing invisibly.
