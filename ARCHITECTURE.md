# Architecture

## Tech stack choices and rationale

- **Backend — Python + FastAPI.** Everything runs as a single process in one language, and it
  serves the frontend too, so there's no separate build step. That makes it easy to run locally
  and just as easy to deploy on a free-tier host like Render, Railway, or Fly.io.
- **Frontend — plain HTML/CSS/JS, no framework.** The UI is just an upload area and four result
  panels (redacted transcript, summary, key points, audit log). A framework would add build
  tooling without giving anything back at this scale.
- **File parsing — `python-docx` for `.docx`, `pdfplumber` for `.pdf`, plain decoding for `.txt`.**
  Straightforward, well-maintained libraries for each format, nothing custom.
- **AI — Groq**, using an open model (`llama-3.3-70b-versatile` by default) on Groq's free tier.
  A local model (via Ollama) would need several GB of RAM that free-tier hosts don't offer, so
  Groq — just an outbound API call — keeps the deployed app small and fast to start. Among hosted
  LLM APIs, Groq's free tier is generous enough for this use case and doesn't require setting up
  billing.

## Redaction strategy: hybrid (rules + LLM)

Two passes, in order:

1. **Regex first.** Emails and phone numbers are caught with plain pattern matching, since they
   have a predictable structure and don't need judgment. This also acts as a safety net — these
   two get redacted even if the AI call fails.
2. **LLM second.** Groq is asked to read the transcript and return a list of sensitive entities
   — names, confidential company names, addresses, non-public financial figures, legal counsel —
   along with why each was flagged and how confident it is. Crucially, the model is never asked
   to rewrite the transcript itself, only to point at what's sensitive.

The actual redaction is done in plain Python: each flagged entity is matched against the
original text and swapped for a typed placeholder (e.g. `[PERSON_NAME]`), longest matches first
so a full name doesn't get partially clobbered by a shorter fragment. Because the model never
touches the transcript text directly, anything it doesn't flag is guaranteed to come back
byte-for-byte unchanged. If the model flags something that can't be found verbatim in the text,
it's not silently dropped — it shows up in the audit log so a human can double check that spot.

## AI model used for summarisation

A second, separate Groq call (same model) generates the summary and key points — and it only
ever sees the **redacted** transcript, never the original. So even if the model were inclined to
mention a name, there's no name in front of it to mention. It's instructed to stick to what was
actually said (no inference) and to refer to speakers by role, never by name.
