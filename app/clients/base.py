"""What every model provider implements, so features don't depend on a particular provider."""
import logging
from collections.abc import AsyncIterator
from typing import Protocol, TypeVar

from fastapi import Depends
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.errors import AppError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ModelClient(Protocol):
    def stream_pdf(self, pdf: bytes, system: str, prompt: str, output: type[BaseModel]) -> AsyncIterator[str]:
        """Sends a PDF with instructions and yields the answer as it is written: JSON matching `output`'s schema.
        Raises AppError if the answer can't be completed."""
        ...


async def review_pdf(model: ModelClient, pdf: bytes, system: str, prompt: str, output: type[T]) -> T:
    """The whole answer, once it is complete."""
    return parse(output, "".join([chunk async for chunk in model.stream_pdf(pdf, system, prompt, output)]))


def parse(output: type[T], text: str) -> T:
    try:
        return output.model_validate_json(text)
    except ValidationError as error:
        logger.error("The model returned JSON that does not match the schema: %s", error)
        raise failed()


def get_model_client(settings: Settings = Depends(get_settings)) -> ModelClient:
    """The client for AI_PROVIDER. SDKs are imported here so an unused provider costs nothing at startup."""
    if settings.ai_provider == "openai":
        if not settings.openai_api_key:
            raise not_configured("OPENAI_API_KEY")
        from app.clients.openai_client import OpenAIModel
        return OpenAIModel(settings)

    if settings.ai_provider == "gemini":
        if not settings.gemini_api_key:
            raise not_configured("GEMINI_API_KEY")
        from app.clients.gemini_client import GeminiModel
        return GeminiModel(settings)

    if settings.ai_provider == "local":
        from app.clients.local_client import LocalModel
        return LocalModel(settings)

    from app.clients.claude import ClaudeModel
    return ClaudeModel(settings)


# Errors shared by every provider, worded for the portal user

def busy() -> AppError:
    return AppError(503, "KAT is busy right now, please try again in a minute.")


def rejected() -> AppError:
    return AppError(422, "The document could not be analysed, it may be too large or have too many pages.")


def failed() -> AppError:
    return AppError(502, "The AI service returned an error, please try again.")


def unreachable() -> AppError:
    return AppError(502, "The AI service could not be reached.")


def declined() -> AppError:
    return AppError(422, "The AI declined to analyse this document.")


def cut_off() -> AppError:
    return AppError(502, "The analysis was cut off before it finished, try a smaller deck.")


def not_configured(setting: str) -> AppError:
    return AppError(500, f"The AI service is not configured: {setting} is missing.")
