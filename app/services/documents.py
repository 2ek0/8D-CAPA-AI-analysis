"""Slide decks are sent to the model as PDF: one page per slide, so the model sees each slide's text and its visuals
(charts, fishbones, tables) and the slide numbers match the previews the portal shows."""
import asyncio
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.errors import AppError

SUPPORTED_EXTENSIONS = (".pptx", ".pdf")


def validate_upload(filename: str, size: int, max_mb: int) -> None:
    if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
        raise AppError(400, "Only .pptx and .pdf files are supported.")
    if size == 0:
        raise AppError(400, "The file is empty.")
    if size > max_mb * 1024 * 1024:
        raise AppError(413, f"The file is larger than {max_mb} MB.")


async def to_pdf(filename: str, data: bytes, soffice_path: str, timeout: int) -> bytes:
    if filename.lower().endswith(".pdf"):
        return data
    return await asyncio.to_thread(_convert_with_libreoffice, data, soffice_path, timeout)


def _convert_with_libreoffice(data: bytes, soffice_path: str, timeout: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "deck.pptx"
        source.write_bytes(data)

        try:
            subprocess.run(
                [soffice_path, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(source)],
                check=True, capture_output=True, timeout=timeout,
            )
        except FileNotFoundError:
            raise AppError(500, "LibreOffice is not installed on the AI service, so .pptx files can't be read.")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise AppError(422, "The slide deck could not be converted, check that it opens in PowerPoint.")

        output = Path(tmp) / "deck.pdf"
        if not output.exists():
            raise AppError(422, "The slide deck could not be converted, check that it opens in PowerPoint.")
        return output.read_bytes()


def count_pages(pdf: bytes) -> int:
    try:
        pages = len(PdfReader(BytesIO(pdf)).pages)
    except (PdfReadError, ValueError):
        raise AppError(422, "The file could not be read as a PDF.")

    if pages == 0:
        raise AppError(422, "The document has no pages.")
    return pages
