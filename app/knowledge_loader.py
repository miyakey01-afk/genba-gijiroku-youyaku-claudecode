import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".text"}
PDF_EXTENSIONS = {".pdf"}


def load_all_knowledge() -> str:
    """Load all knowledge files from the configured directory.

    Returns concatenated text from all .txt/.md/.csv/.pdf files,
    each prefixed with a filename header.
    """
    knowledge_dir = Path(settings.knowledge_dir)
    if not knowledge_dir.exists():
        logger.warning("Knowledge directory not found: %s", knowledge_dir)
        return ""

    sections = []
    for path in sorted(knowledge_dir.iterdir()):
        if path.is_dir():
            continue

        ext = path.suffix.lower()
        text = ""

        if ext in TEXT_EXTENSIONS:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("Failed to read %s: %s", path.name, e)
                continue

        elif ext in PDF_EXTENSIONS:
            try:
                import fitz  # pymupdf

                doc = fitz.open(str(path))
                pages = [page.get_text() for page in doc]
                doc.close()
                text = "\n".join(pages)
            except ImportError:
                logger.warning("pymupdf not installed, skipping PDF: %s", path.name)
                continue
            except Exception as e:
                logger.warning("Failed to read PDF %s: %s", path.name, e)
                continue
        else:
            continue

        if text.strip():
            sections.append(f"=== {path.name} ===\n{text.strip()}")

    return "\n\n".join(sections)
