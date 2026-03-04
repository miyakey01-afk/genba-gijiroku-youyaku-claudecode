import os
import time
import tempfile
from pathlib import Path
from typing import AsyncGenerator

from google import genai
from google.genai import types

from app.config import settings
from app.prompts import (
    SYSTEM_PROMPT, USER_PROMPT_TEMPLATE,
    RUCAS_SYSTEM_PROMPT, RUCAS_USER_PROMPT_TEMPLATE,
    PROPOSAL_EXTRACT_PROMPT, PROPOSAL_SEARCH_PROMPT,
    PROPOSAL_SYSTEM_PROMPT, PROPOSAL_USER_PROMPT_TEMPLATE,
)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".webm"}
TEXT_EXTENSIONS = {".txt", ".md", ".text", ".csv"}

AUDIO_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
    ".webm": "audio/webm",
}


def _get_client() -> genai.Client:
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=600_000),  # 10 minutes
    )


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
    mode: str = "minutes",
) -> str:
    """Generate meeting minutes from text and/or audio files.

    Args:
        text_paste: Pasted text content.
        file_paths: List of (original_filename, temp_file_path) tuples.
        status_callback: Async callable for progress updates.
        mode: "minutes" for full meeting minutes, "rucas" for short CRM summary.

    Returns:
        Generated markdown text (minutes) or plain text (rucas).
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

        # Build user prompt (select template based on mode)
        all_text = "\n\n".join(text_parts)
        if text_paste:
            all_text = text_paste + "\n\n" + all_text if all_text else text_paste

        if mode == "rucas":
            sys_prompt = RUCAS_SYSTEM_PROMPT
            user_prompt = RUCAS_USER_PROMPT_TEMPLATE.format(
                content=all_text if all_text else "（音声ファイルを参照してください）"
            )
        else:
            sys_prompt = SYSTEM_PROMPT
            user_prompt = USER_PROMPT_TEMPLATE.format(
                content=all_text if all_text else "（音声ファイルを参照してください）"
            )
        contents.append(types.Part.from_text(text=user_prompt))

        # Call Gemini with retry on transient network errors only
        max_retries = 2
        for attempt in range(1, max_retries + 1):
            try:
                if attempt == 1:
                    await send_status("議事録を生成中...")
                else:
                    await send_status(f"議事録を生成中...（リトライ {attempt}/{max_retries}）")

                response = client.models.generate_content(
                    model=settings.gemini_model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_prompt,
                        temperature=0.3 if mode == "rucas" else 0.7,
                    ),
                )
                return response.text

            except (ConnectionError, TimeoutError, OSError) as e:
                if attempt == max_retries:
                    raise
                await send_status(f"接続エラーが発生、{attempt * 5}秒後にリトライします...")
                time.sleep(attempt * 5)

    finally:
        # Clean up uploaded files from Gemini
        for uf in uploaded_files:
            try:
                client.files.delete(name=uf.name)
            except Exception:
                pass


async def generate_proposal(
    sales_memo: str,
    company: str,
    proposal_date: str,
    area: str,
    category: str,
    status_callback=None,
) -> str:
    """Generate a proposal draft through a 4-step pipeline.

    Step 1: Extract proposal essence from sales_memo (if long)
    Step 2: Web search for customer company info (Google Search grounding)
    Step 3: Load product knowledge files
    Step 4: Generate proposal draft
    """
    from app.knowledge_loader import load_all_knowledge

    client = _get_client()

    async def send_status(msg: str):
        if status_callback:
            await status_callback(msg)

    # --- Step 1: Extract proposal essence if text is long ---
    await send_status("議事録から提案エッセンスを抽出中...")

    if len(sales_memo) > 2000:
        extract_prompt = PROPOSAL_EXTRACT_PROMPT.format(content=sales_memo)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[types.Part.from_text(text=extract_prompt)],
            config=types.GenerateContentConfig(temperature=0.3),
        )
        extracted_memo = response.text
    else:
        extracted_memo = sales_memo

    # --- Step 2: Web search for customer company info ---
    await send_status(f"顧客企業「{company}」をWeb検索中...")

    search_prompt = PROPOSAL_SEARCH_PROMPT.format(
        company=company, area=area,
    )
    try:
        search_response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[types.Part.from_text(text=search_prompt)],
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.3,
            ),
        )
        web_search_result = search_response.text
    except Exception as e:
        await send_status(f"Web検索でエラーが発生しました。検索結果なしで続行します。")
        web_search_result = f"（Web検索に失敗しました: {e}）"

    # --- Step 3: Load product knowledge ---
    await send_status("自社商材ナレッジを読み込み中...")

    knowledge_text = load_all_knowledge()
    if not knowledge_text:
        knowledge_text = "（ナレッジファイルが見つかりませんでした）"

    # --- Step 4: Generate proposal draft ---
    await send_status("提案書草案を生成中...")

    sys_prompt = PROPOSAL_SYSTEM_PROMPT.format(
        web_search_result=web_search_result,
        knowledge=knowledge_text,
        company=company,
        area=area,
        category=category,
        sales_memo=extracted_memo,
        proposal_date=proposal_date,
    )

    user_prompt = PROPOSAL_USER_PROMPT_TEMPLATE.format(
        web_search_result=web_search_result,
        company=company,
        area=area,
        category=category,
        proposal_date=proposal_date,
        sales_memo=extracted_memo,
    )

    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                await send_status(f"提案書草案を生成中...（リトライ {attempt}/{max_retries}）")

            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=[types.Part.from_text(text=user_prompt)],
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    temperature=0.7,
                ),
            )
            return response.text

        except (ConnectionError, TimeoutError, OSError):
            if attempt == max_retries:
                raise
            await send_status(f"接続エラーが発生、{attempt * 5}秒後にリトライします...")
            time.sleep(attempt * 5)
