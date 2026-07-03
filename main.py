from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from redaction.groq_client import AIProcessingError, RateLimitedError
from redaction.parsing import EmptyFileError, UnsupportedFileTypeError, parse_file
from redaction.pipeline import process_transcript

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Transcript Redaction Portal")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/process")
async def process(file: UploadFile = File(...)) -> JSONResponse:
    # Note: nothing in this handler logs the raw transcript, the redacted
    # transcript, or any audit entry content — only generic, non-sensitive
    # error messages are ever surfaced or (implicitly, via FastAPI's default
    # access log) recorded. No database, no file persistence.
    try:
        content = await file.read()
        raw_text = parse_file(file.filename or "", content)
        result = process_transcript(raw_text)

        return JSONResponse(
            {
                "redactedText": result.redacted_text,
                "summary": result.summary,
                "keyPoints": result.key_points,
                "auditLog": [
                    {
                        "type": entry.type,
                        "originalText": entry.original_text,
                        "placeholder": entry.placeholder,
                        "reason": entry.reason,
                        "confidence": entry.confidence,
                        "detectedBy": entry.detected_by,
                        "status": entry.status,
                    }
                    for entry in result.audit_log
                ],
            }
        )
    except UnsupportedFileTypeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except EmptyFileError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RateLimitedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=429)
    except AIProcessingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    except Exception:  # noqa: BLE001 - last-resort guard so a bug never crashes the request
        return JSONResponse(
            {"error": "Something went wrong while processing the transcript. Please try again."},
            status_code=500,
        )
