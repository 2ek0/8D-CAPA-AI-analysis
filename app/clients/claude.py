import base64
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.clients.base import busy, cut_off, declined, failed, rejected, unreachable
from app.config import Settings

logger = logging.getLogger(__name__)


@lru_cache
def get_claude_client(api_key: str, timeout: float) -> AsyncAnthropic:
    # api_key=None lets the SDK fall back to ANTHROPIC_API_KEY or an `ant auth login` profile
    return AsyncAnthropic(api_key=api_key or None, timeout=timeout)


def request_options(settings: Settings) -> dict[str, Any]:
    """Model settings shared by every feature, so they are configured in one place."""
    options: dict[str, Any] = {
        "model": settings.claude_model,
        "max_tokens": settings.claude_max_tokens,
        "output_config": {"effort": settings.claude_effort},
    }

    if settings.claude_refusal_fallback:
        # On a policy decline the API re-runs the request on a suitable fallback model within the same call
        options["betas"] = ["server-side-fallback-2026-07-01"]
        options["fallbacks"] = "default"

    return options


class ClaudeModel:
    def __init__(self, settings: Settings):
        self.client = get_claude_client(settings.anthropic_api_key, settings.ai_timeout_seconds)
        self.settings = settings

    async def stream_pdf(self, pdf: bytes, system: str, prompt: str, output: type[BaseModel]) -> AsyncIterator[str]:
        try:
            async with self.client.beta.messages.stream(
                **request_options(self.settings),
                system=system,
                output_format=output,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                message = await stream.get_final_message()
        except anthropic.RateLimitError:
            raise busy()
        except anthropic.BadRequestError as error:
            logger.error("Claude rejected the request: %s", error)
            raise rejected()
        except anthropic.APIStatusError as error:
            logger.error("Claude returned %s: %s", error.status_code, error)
            raise failed()
        except anthropic.APIConnectionError:
            raise unreachable()

        logger.info(
            "Claude %s done: %d input / %d output tokens, stop_reason=%s",
            self.settings.claude_model, message.usage.input_tokens, message.usage.output_tokens, message.stop_reason,
        )

        if message.stop_reason == "refusal":
            raise declined()
        if message.stop_reason == "max_tokens":
            raise cut_off()
