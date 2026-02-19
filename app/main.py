import json
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.docx_generator import markdown_to_docx
from app.gemini_client import generate_minutes

app = FastAPI(title="議事録作成アプリ")

# Temp directory for DOCX downloads
DOWNLOAD_DIR = Path(settings.temp_dir) / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/generate")
async def generate(
    text_paste: str = Form(default=""),
    output_format: str = Form(default="text"),
    files: list[UploadFile] = File(default=[]),
):
    async def event_stream():
        temp_files: list[tuple[str, str]] = []
        temp_dir = None

        try:
            yield _sse_event("status", {"message": "入力を処理中..."})

            # Validate input
            has_text = bool(text_paste.strip())
            has_files = bool(files and any(f.filename for f in files))

            if not has_text and not has_files:
                yield _sse_event("error", {"message": "テキストまたはファイルを入力してください。"})
                return

            # Save uploaded files to temp directory
            if has_files:
                temp_dir = tempfile.mkdtemp(dir=settings.temp_dir)
                for f in files:
                    if not f.filename:
                        continue
                    yield _sse_event("status", {"message": f"ファイルを保存中: {f.filename}"})
                    temp_path = os.path.join(temp_dir, f.filename)
                    content = await f.read()
                    with open(temp_path, "wb") as out:
                        out.write(content)
                    temp_files.append((f.filename, temp_path))

            # Status callback for Gemini client
            async def on_status(msg: str):
                pass  # SSE events are yielded from this generator only

            # Generate minutes
            yield _sse_event("status", {"message": "Gemini APIで議事録を生成中..."})

            # Use a simple approach: run synchronously since Gemini SDK is sync
            import asyncio

            loop = asyncio.get_event_loop()

            async def status_collector(msg: str):
                nonlocal status_messages
                status_messages.append(msg)

            status_messages: list[str] = []
            markdown_result = await asyncio.to_thread(
                _generate_sync, text_paste, temp_files, status_messages
            )

            # Send any collected status messages
            for msg in status_messages:
                yield _sse_event("status", {"message": msg})

            # Handle output format
            download_url = None
            if output_format == "word":
                yield _sse_event("status", {"message": "DOCXファイルを生成中..."})
                docx_buffer = markdown_to_docx(markdown_result)
                filename = f"議事録_{uuid.uuid4().hex[:8]}.docx"
                docx_path = DOWNLOAD_DIR / filename
                with open(docx_path, "wb") as out:
                    out.write(docx_buffer.read())
                download_url = f"/api/download/{filename}"

            yield _sse_event("result", {
                "markdown": markdown_result,
                "download_url": download_url,
            })

        except Exception as e:
            yield _sse_event("error", {"message": f"エラーが発生しました: {str(e)}"})

        finally:
            # Clean up temp files
            if temp_dir:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _generate_sync(
    text_paste: str,
    file_paths: list[tuple[str, str]],
    status_messages: list[str],
) -> str:
    """Synchronous wrapper for generate_minutes (runs in thread)."""
    import asyncio

    async def status_cb(msg: str):
        status_messages.append(msg)

    return asyncio.run(
        generate_minutes(text_paste, file_paths, status_callback=status_cb)
    )


@app.get("/api/download/{filename}")
async def download(filename: str):
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists():
        return {"error": "ファイルが見つかりません"}
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# Serve static files (frontend) — mount last so API routes take priority
static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
