# Transcript Redaction Portal

## Setup

### 1. Create a virtual environment and install dependencies

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1      # Windows
# source .venv/bin/activate      # macOS/Linux

pip install -r requirements.txt
```

### 2. Configure your Groq API key

Copy `.env.example` to `.env` and add your key (free tier — get one at
[console.groq.com/keys](https://console.groq.com/keys)):

```bash
cp .env.example .env
```

```
GROQ_API_KEY=your-groq-api-key-here
```


### 3. Run

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Visit `http://localhost:8000`.

Notes:
- Use `python -m uvicorn`, not a bare `uvicorn` command — this works regardless of whether
  uvicorn's script is on your PATH, since it just uses whichever `python` is currently active.
- Don't use `--reload` on Windows — it can crash with a multiprocessing permission error.
  Restart the process manually after making code changes instead.

### 4. Try it

Upload any file from `samples/` (three transcripts covering different companies, currencies,
and PII mixes).

## Environment variables

| `GROQ_API_KEY` | Authenticates with Groq's API. Never committed — read from the environment only (see `redaction/groq_client.py`). |
No other secrets are required. There is no database, no external storage, and no other third-party service in this app.

See `ARCHITECTURE.md` for the tech stack, redaction strategy, and API used.
