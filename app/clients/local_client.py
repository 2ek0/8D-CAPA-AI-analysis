"""A model running on this machine through Ollama (https://ollama.com).

Local models can't read PDFs, so each page's text is extracted and, if LOCAL_SEND_IMAGES is on, each page is also
rendered to an image for vision models (gemma3, qwen2.5vl). The native Ollama API is used rather than its
OpenAI-compatible one because only the native API can raise the context size (num_ctx), which a full deck needs."""
import base64
import json
import logging
from collections.abc import AsyncIterator
from io import BytesIO

import httpx
from pydantic import BaseModel
from pypdf import PdfReader

from app.clients.base import cut_off, failed
from app.config import Settings
from app.errors import AppError

logger = logging.getLogger(__name__)

# Long side of a rendered slide in pixels; vision models scale images to about this size anyway
IMAGE_SIZE = 896


def slide_texts(pdf: bytes) -> list[str]:
    return [(page.extract_text() or "").strip() for page in PdfReader(BytesIO(pdf)).pages]


def slide_images(pdf: bytes) -> list[str]:
    import pypdfium2  # only needed when images are sent

    images = []
    document = pypdfium2.PdfDocument(pdf)
    try:
        for page in document:
            scale = IMAGE_SIZE / max(page.get_width(), page.get_height())
            buffer = BytesIO()
            page.render(scale=scale).to_pil().save(buffer, format="PNG")
            images.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    finally:
        document.close()
    return images


def slides_message(texts: list[str], with_images: bool) -> str:
    source = "text and an image of each slide, in order" if with_images else "the text of each slide; visuals are not available"
    slides = "\n\n".join(f"--- Slide {number} ---\n{text or '(no text)'}" for number, text in enumerate(texts, 1))
    return f"The report is given as {source}.\n\n{slides}"


class LocalModel:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream_pdf(self, pdf: bytes, system: str, prompt: str, output: type[BaseModel]) -> AsyncIterator[str]:
        texts = slide_texts(pdf)
        message = {"role": "user", "content": slides_message(texts, self.settings.local_send_images) + "\n\n" + prompt}
        if self.settings.local_send_images:
            message["images"] = slide_images(pdf)

        request = {
            "model": self.settings.local_model,
            "messages": [{"role": "system", "content": system}, message],
            "format": output.model_json_schema(),
            "stream": True,
            "options": {"num_ctx": self.settings.local_context_tokens, "temperature": 0},
        }

        last = {}
        try:
            # The timeout applies between streamed lines, so a long review is fine as long as tokens keep coming
            async with httpx.AsyncClient(base_url=self.settings.local_base_url, timeout=self.settings.ai_timeout_seconds) as client:
                async with client.stream("POST", "/api/chat", json=request) as response:
                    if response.status_code == 404:
                        raise AppError(500, f"The local model {self.settings.local_model} is not downloaded, run: ollama pull {self.settings.local_model}")
                    if response.is_error:
                        logger.error("Ollama returned %s: %s", response.status_code, (await response.aread()).decode(errors="replace"))
                        raise failed()

                    # One JSON object per line, the last with done: true and the token counts
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        last = json.loads(line)
                        if "error" in last:
                            logger.error("Ollama failed: %s", last["error"])
                            raise failed()
                        if content := last.get("message", {}).get("content"):
                            yield content
        except httpx.TimeoutException:
            raise AppError(504, "The local AI took too long, try a smaller deck or raise AI_TIMEOUT_SECONDS.")
        except httpx.TransportError:
            raise AppError(502, "The local AI could not be reached, check that Ollama is running.")

        logger.info(
            "%s done: %s input / %s output tokens in %.0fs, done_reason=%s",
            self.settings.local_model, last.get("prompt_eval_count"), last.get("eval_count"),
            last.get("total_duration", 0) / 1e9, last.get("done_reason"),
        )

        if last.get("done_reason") == "length":
            raise cut_off()
