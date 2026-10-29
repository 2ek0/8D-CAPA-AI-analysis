import logging
from collections.abc import AsyncIterator
from functools import lru_cache

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.clients.base import busy, cut_off, declined, failed, rejected, unreachable
from app.config import Settings

logger = logging.getLogger(__name__)

BLOCKED = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
    types.FinishReason.RECITATION,
}


@lru_cache
def get_gemini_client(api_key: str, timeout: float) -> genai.Client:
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout * 1000)))


class GeminiModel:
    def __init__(self, settings: Settings):
        self.client = get_gemini_client(settings.gemini_api_key, settings.ai_timeout_seconds)
        self.settings = settings

    async def stream_pdf(self, pdf: bytes, system: str, prompt: str, output: type[BaseModel]) -> AsyncIterator[str]:
        last = None
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=self.settings.gemini_model,
                contents=[types.Part.from_bytes(data=pdf, mime_type="application/pdf"), prompt],
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=output.model_json_schema(),
                ),
            )
            async for chunk in stream:
                if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                    raise declined()
                if chunk.text:
                    yield chunk.text
                last = chunk
        except errors.ClientError as error:
            if error.code == 429:
                raise busy()
            logger.error("%s rejected the request: %s", self.settings.gemini_model, error)
            raise rejected()
        except errors.APIError as error:
            logger.error("%s returned %s: %s", self.settings.gemini_model, error.code, error)
            raise failed()
        except httpx.TransportError:
            raise unreachable()

        candidate = last.candidates[0] if last and last.candidates else None
        finish_reason = candidate.finish_reason if candidate else None
        usage = last.usage_metadata if last else None
        logger.info(
            "%s done: %s input / %s output / %s thinking tokens, finish_reason=%s",
            self.settings.gemini_model, usage and usage.prompt_token_count, usage and usage.candidates_token_count,
            usage and usage.thoughts_token_count, finish_reason,
        )

        if finish_reason in BLOCKED:
            raise declined()
        if finish_reason == types.FinishReason.MAX_TOKENS:
            raise cut_off()
