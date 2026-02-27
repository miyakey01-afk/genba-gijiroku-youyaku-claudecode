import asyncio
import concurrent.futures
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.docx_generator import markdown_to_docx
from app.gemini_client import generate_minutes

JST = timezone(timedelta(hours=9))


def _extract_title(markdown: str) -> str:
    """Extract title from the first '# ...' line of markdown."""
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return "商談議事録"


def _sanitize_filename(name: str) -> str:
    """Remove characters not safe for filenames."""
    return re.sub(r'[\\/:*?"<>|\s　]+', "_", name).strip("_")


def _add_created_at(markdown: str, now: datetime) -> str:
    """Insert '作成日時' line right after the title '# ...' line."""
    date_str = now.strftime("%Y年%m月%d日 %H:%M")
    lines = markdown.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip().startswith("# "):
            insert_line = f"\n**作成日時:** {date_str}\n"
            lines.insert(i + 1, insert_line)
            break
    else:
        # No title found — prepend date at the top
        lines.insert(0, f"**作成日時:** {date_str}\n\n")
    return "".join(lines)

app = FastAPI(title="RAMMY 議事録作成アプリ")

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
    mode: str = Form(default="minutes"),
    files: list[UploadFile] = File(default=[]),
):
    # Read all uploaded files BEFORE entering the SSE generator.
    # FastAPI closes the request body (and UploadFile handles) once
    # StreamingResponse is returned, so reading inside the generator
    # causes "read of closed file" errors.
    file_data: list[tuple[str, bytes]] = []
    has_files = bool(files and any(f.filename for f in files))
    if has_files:
        for f in files:
            if not f.filename:
                continue
            file_data.append((f.filename, await f.read()))

    async def event_stream():
        temp_files: list[tuple[str, str]] = []
        temp_dir = None

        try:
            yield _sse_event("status", {"message": "入力を処理中...", "progress": 5})

            # Validate input
            has_text = bool(text_paste.strip())

            if not has_text and not file_data:
                yield _sse_event("error", {"message": "テキストまたはファイルを入力してください。"})
                return

            # Save pre-read file data to temp directory
            if file_data:
                temp_dir = tempfile.mkdtemp(dir=settings.temp_dir)
                total_files = len(file_data)
                for idx, (filename, content) in enumerate(file_data):
                    file_progress = 10 + int(20 * idx / max(total_files, 1))
                    yield _sse_event("status", {"message": f"ファイルを保存中: {filename}", "progress": file_progress})
                    temp_path = os.path.join(temp_dir, filename)
                    with open(temp_path, "wb") as out:
                        out.write(content)
                    temp_files.append((filename, temp_path))

            # Generate minutes / RUCAS summary
            if mode == "rucas":
                yield _sse_event("status", {"message": "Gemini APIでRUCAS営業情報を生成中...", "progress": 30})
            else:
                yield _sse_event("status", {"message": "Gemini APIで議事録を生成中...", "progress": 30})

            status_messages: list[str] = []

            # Run generation in a thread, sending SSE keepalive every 15s
            # to prevent Cloud Run / browser from closing the idle connection
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    _generate_sync, text_paste, temp_files, status_messages, mode
                )
                elapsed = 0
                while not future.done():
                    await asyncio.sleep(3)
                    elapsed += 3
                    if not future.done() and elapsed % 15 == 0:
                        # SSE comment — keeps HTTP connection alive,
                        # ignored by frontend event parser
                        yield ": keepalive\n\n"

                markdown_result = future.result()

            # Send any collected status messages with progress
            for i, msg in enumerate(status_messages):
                msg_progress = 35 + int(40 * (i + 1) / max(len(status_messages), 1))
                yield _sse_event("status", {"message": msg, "progress": msg_progress})

            yield _sse_event("status", {"message": "出力を準備中...", "progress": 85})

            now = datetime.now(JST)
            date_stamp = now.strftime("%Y%m%d_%H%M")

            if mode == "rucas":
                # RUCAS mode: single-line plain text for CRM paste
                markdown_result = markdown_result.strip().replace("\n", "").replace("\r", "")
                file_base = f"RUCAS営業情報_{date_stamp}"
                filename = f"{file_base}.txt"
                txt_path = DOWNLOAD_DIR / filename
                with open(txt_path, "w", encoding="utf-8") as out:
                    out.write(markdown_result)
                download_url = f"/api/download/{quote(filename)}"

                yield _sse_event("result", {
                    "markdown": markdown_result,
                    "download_url": download_url,
                    "output_format": "text",
                })
            else:
                # Minutes mode: add title and creation datetime
                title = _extract_title(markdown_result)
                markdown_result = _add_created_at(markdown_result, now)

                safe_title = _sanitize_filename(title)
                file_base = f"{safe_title}_{date_stamp}"

                # Handle output format
                download_url = None
                if output_format == "word":
                    yield _sse_event("status", {"message": "DOCXファイルを生成中...", "progress": 90})
                    docx_buffer = markdown_to_docx(markdown_result)
                    filename = f"{file_base}.docx"
                    docx_path = DOWNLOAD_DIR / filename
                    with open(docx_path, "wb") as out:
                        out.write(docx_buffer.read())
                    download_url = f"/api/download/{quote(filename)}"
                else:
                    filename = f"{file_base}.txt"
                    txt_path = DOWNLOAD_DIR / filename
                    with open(txt_path, "w", encoding="utf-8") as out:
                        out.write(markdown_result)
                    download_url = f"/api/download/{quote(filename)}"

                yield _sse_event("result", {
                    "markdown": markdown_result,
                    "download_url": download_url,
                    "output_format": output_format,
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
    mode: str = "minutes",
) -> str:
    """Synchronous wrapper for generate_minutes (runs in thread)."""
    import asyncio

    async def status_cb(msg: str):
        status_messages.append(msg)

    return asyncio.run(
        generate_minutes(text_paste, file_paths, status_callback=status_cb, mode=mode)
    )


@app.get("/api/download/{filename:path}")
async def download(filename: str):
    filename = unquote(filename)
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists():
        return {"error": "ファイルが見つかりません"}
    if filename.endswith(".txt"):
        media_type = "text/plain; charset=utf-8"
    else:
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=media_type,
    )


# Serve static files (frontend) — mount last so API routes take priority
static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
