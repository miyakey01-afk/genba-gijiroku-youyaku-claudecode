import os
import time
import tempfile
from pathlib import Path
from typing import AsyncGenerator

from google import genai
from google.genai import types

from app.config import settings
from app.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
TEXT_EXTENSIONS = {".txt", ".md", ".text", ".csv"}

AUDIO_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
}


def _get_client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


def _classify_file(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "unknown"


async def generate_minutes(
    text_paste: str,
    file_paths: list[tuple[str, str]],
    status_callback=None,
) -> str:
    """Generate meeting minutes from text and/or audio files.

    Args:
        text_paste: Pasted text content.
        file_paths: List of (original_filename, temp_file_path) tuples.
        status_callback: Async callable for progress updates.

    Returns:
        Generated markdown text.
    """
    client = _get_client()
    contents = []
    text_parts = []
    uploaded_files = []

    async def send_status(msg: str):
        if status_callback:
            await status_callback(msg)

    try:
        # Process files
        for original_name, temp_path in file_paths:
            file_type = _classify_file(original_name)

            if file_type == "audio":
                await send_status(f"音声ファイルをアップロード中: {original_name}")
                ext = Path(original_name).suffix.lower()
                mime_type = AUDIO_MIME_TYPES.get(ext, "audio/mpeg")

                uploaded = client.files.upload(
                    file=temp_path,
                    config=types.UploadFileConfig(
                        mime_type=mime_type,
                        display_name=original_name,
                    ),
                )

                await send_status("音声ファイルの処理を待機中...")
                while uploaded.state == "PROCESSING":
                    time.sleep(3)
                    uploaded = client.files.get(name=uploaded.name)

                if uploaded.state == "FAILED":
                    raise RuntimeError(
                        f"音声ファイルの処理に失敗しました: {original_name}"
                    )

                uploaded_files.append(uploaded)
                contents.append(
                    types.Part.from_uri(
                        file_uri=uploaded.uri,
                        mime_type=mime_type,
                    )
                )

            elif file_type == "text":
                await send_status(f"テキストファイルを読み込み中: {original_name}")
                with open(temp_path, "r", encoding="utf-8", errors="replace") as f:
                    text_parts.append(f.read())

            else:
                await send_status(f"未対応のファイル形式をスキップ: {original_name}")

        # Build user prompt
        all_text = "\n\n".join(text_parts)
        if text_paste:
            all_text = text_paste + "\n\n" + all_text if all_text else text_paste

        user_prompt = USER_PROMPT_TEMPLATE.format(content=all_text if all_text else "（音声ファイルを参照してください）")
        contents.append(types.Part.from_text(text=user_prompt))

        # Call Gemini
        await send_status("議事録を生成中...")
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
            ),
        )

        return response.text

    finally:
        # Clean up uploaded files from Gemini
        for uf in uploaded_files:
            try:
                client.files.delete(name=uf.name)
            except Exception:
                pass
