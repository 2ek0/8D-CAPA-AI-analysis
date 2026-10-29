import base64
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.clients.base import busy, cut_off, declined, failed, rejected, unreachable
from app.config import Settings

logger = logging.getLogger(__name__)


@lru_cache
def get_openai_client(api_key: str, base_url: str, timeout: float) -> AsyncOpenAI:
    # An empty base_url means OpenAI itself; anything else is an OpenAI-compatible service such as OpenRouter
    return AsyncOpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout)


class OpenAIModel:
    def __init__(self, settings: Settings):
        self.client = get_openai_client(
            settings.openai_api_key, settings.openai_base_url, settings.ai_timeout_seconds,
        )
        self.settings = settings

    async def stream_pdf(self, pdf: bytes, system: str, prompt: str, output: type[BaseModel]) -> AsyncIterator[str]:
        options: dict[str, Any] = {}
        if self.settings.openai_reasoning_effort:
            # Only reasoning models accept this, so it is sent only when set
            options["reasoning_effort"] = self.settings.openai_reasoning_effort

        try:
            async with self.client.chat.completions.stream(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": [
                        {
                            "type": "file",
                            "file": {
                                "filename": "report.pdf",
                                "file_data": "data:application/pdf;base64," + base64.b64encode(pdf).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ]},
                ],
                response_format=output,
                **options,
            ) as stream:
                async for event in stream:
                    if event.type == "content.delta":
                        yield event.delta
                completion = await stream.get_final_completion()
        except openai.LengthFinishReasonError:
            raise cut_off()
        except openai.ContentFilterFinishReasonError:
            raise declined()
        except ValidationError as error:
            # Strict schemas prevent this on OpenAI, but compatible services may not enforce them
            logger.error("%s returned JSON that does not match the schema: %s", self.settings.openai_model, error)
            raise failed()
        except openai.RateLimitError:
            raise busy()
        except openai.BadRequestError as error:
            logger.error("%s rejected the request: %s", self.settings.openai_model, error)
            raise rejected()
        except openai.APIStatusError as error:
            logger.error("%s returned %s: %s", self.settings.openai_model, error.status_code, error)
            raise failed()
        except openai.APIConnectionError:
            raise unreachable()

        choice = completion.choices[0]
        usage = completion.usage
        logger.info(
            "%s done: %s input / %s output tokens, finish_reason=%s",
            self.settings.openai_model, usage and usage.prompt_tokens, usage and usage.completion_tokens,
            choice.finish_reason,
        )

        if choice.message.refusal:
            raise declined()
