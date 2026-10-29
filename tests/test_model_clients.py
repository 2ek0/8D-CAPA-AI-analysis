import json
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from google.genai import types
from pypdf import PdfWriter

from app.clients import local_client
from app.clients.base import get_model_client, review_pdf
from app.clients.claude import ClaudeModel
from app.clients.gemini_client import GeminiModel
from app.clients.openai_client import OpenAIModel
from app.config import Settings
from app.errors import AppError
from app.features.capa_8d.schemas import Review

pytestmark = pytest.mark.anyio

REVIEW_JSON ='{"headline": "Solid", "slides": [], "summary": {"overview": "Ok", "strengths": [], "gaps": [], "nextActions": []}}'


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings(**values):
    return Settings(_env_file=None, **values)


def chunks(text, size=7):
    return [text[i:i + size] for i in range(0, len(text), size)]


async def stream_of(items):
    for item in items:
        yield item


class FakeOpenAIStream:
    """Stands in for client.chat.completions.stream(): streams content deltas, then the final completion."""

    def __init__(self, text, refusal=None):
        self.text = text
        self.refusal = refusal
        self.request = None

    def __call__(self, **request):
        self.request = request
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *error):
        return False

    def __aiter__(self):
        return stream_of(SimpleNamespace(type="content.delta", delta=chunk) for chunk in chunks(self.text))

    async def get_final_completion(self):
        message = SimpleNamespace(refusal=self.refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


@pytest.mark.parametrize("provider, keys, expected", [
    ("claude", {}, ClaudeModel),
    ("openai", {"openai_api_key": "key"}, OpenAIModel),
    ("gemini", {"gemini_api_key": "key"}, GeminiModel),
    ("local", {}, local_client.LocalModel),
])
def test_selects_the_configured_provider(provider, keys, expected):
    assert isinstance(get_model_client(settings(ai_provider=provider, **keys)), expected)


@pytest.mark.parametrize("provider, setting", [("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")])
def test_a_missing_key_is_reported(provider, setting):
    with pytest.raises(AppError) as error:
        get_model_client(settings(ai_provider=provider))

    assert error.value.status_code == 500
    assert setting in error.value.message


def openai_model(fake):
    model = OpenAIModel(settings(openai_api_key="key", openai_model="gpt-test"))
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(stream=fake)))
    return model


async def test_openai_streams_the_review():
    fake = FakeOpenAIStream(REVIEW_JSON)

    review = await review_pdf(openai_model(fake), b"%PDF", "system", "prompt", Review)

    assert review.headline == "Solid"
    assert fake.request["model"] == "gpt-test"
    assert fake.request["response_format"] is Review
    assert "reasoning_effort" not in fake.request


async def test_openai_refusal_is_declined():
    with pytest.raises(AppError) as error:
        await review_pdf(openai_model(FakeOpenAIStream("", refusal="no")), b"%PDF", "system", "prompt", Review)

    assert error.value.status_code == 422


def gemini_model(text, finish_reason=types.FinishReason.STOP):
    parts = [
        SimpleNamespace(text=chunk, prompt_feedback=None, usage_metadata=None, candidates=[SimpleNamespace(finish_reason=None)])
        for chunk in chunks(text or "")
    ]
    parts.append(SimpleNamespace(text=None, prompt_feedback=None, usage_metadata=None, candidates=[SimpleNamespace(finish_reason=finish_reason)]))
    requests = []

    async def generate_content_stream(**request):
        requests.append(request)
        return stream_of(parts)

    model = GeminiModel(settings(gemini_api_key="key", gemini_model="gemini-test"))
    model.client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=generate_content_stream)))
    return model, requests


async def test_gemini_streams_the_review():
    model, requests = gemini_model(REVIEW_JSON)

    review = await review_pdf(model, b"%PDF", "system", "prompt", Review)

    assert review.headline == "Solid"
    assert requests[0]["model"] == "gemini-test"
    assert requests[0]["config"].response_json_schema == Review.model_json_schema()


@pytest.mark.parametrize("finish_reason, status_code", [
    (types.FinishReason.SAFETY, 422),
    (types.FinishReason.MAX_TOKENS, 502),
])
async def test_gemini_maps_unfinished_answers(finish_reason, status_code):
    model, _ = gemini_model(None, finish_reason)

    with pytest.raises(AppError) as error:
        await review_pdf(model, b"%PDF", "system", "prompt", Review)

    assert error.value.status_code == status_code


async def test_invalid_json_is_an_error():
    model, _ = gemini_model('{"headline": 1}')

    with pytest.raises(AppError) as error:
        await review_pdf(model, b"%PDF", "system", "prompt", Review)

    assert error.value.status_code == 502


def blank_pdf(pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=720, height=405)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def ollama_lines(text, done_reason="stop"):
    """An Ollama streamed reply: one JSON object per line, the last marked done."""
    lines = [{"message": {"content": chunk}, "done": False} for chunk in chunks(text)]
    lines.append({"message": {"content": ""}, "done": True, "done_reason": done_reason})
    return httpx.Response(200, content="\n".join(json.dumps(line) for line in lines))


def local_model(monkeypatch, reply, **values):
    """A LocalModel whose Ollama calls are answered by `reply(request_json)`."""
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return reply(requests[-1])

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        local_client.httpx, "AsyncClient",
        lambda **options: real_client(transport=httpx.MockTransport(handler), **options),
    )
    return local_client.LocalModel(settings(ai_provider="local", **values)), requests


async def test_local_sends_slide_text_and_returns_the_review(monkeypatch):
    model, requests = local_model(
        monkeypatch, lambda _: ollama_lines(REVIEW_JSON),
    )

    review = await review_pdf(model, blank_pdf(2), "system", "prompt", Review)

    assert review.headline == "Solid"
    request = requests[0]
    assert request["model"] == "gemma3:4b"
    assert request["format"] == Review.model_json_schema()
    assert request["stream"] is True
    assert "--- Slide 2 ---" in request["messages"][1]["content"]
    assert "images" not in request["messages"][1]


async def test_local_sends_one_image_per_slide_when_enabled(monkeypatch):
    model, requests = local_model(
        monkeypatch, lambda _: ollama_lines(REVIEW_JSON),
        local_send_images=True,
    )

    await review_pdf(model, blank_pdf(3), "system", "prompt", Review)

    assert len(requests[0]["messages"][1]["images"]) == 3


@pytest.mark.parametrize("response, status_code", [
    (httpx.Response(404, json={"error": "model not found"}), 500),
    (ollama_lines("{}", done_reason="length"), 502),
    (httpx.Response(200, content=json.dumps({"error": "out of memory"})), 502),
])
async def test_local_maps_errors(monkeypatch, response, status_code):
    model, _ = local_model(monkeypatch, lambda _: response)

    with pytest.raises(AppError) as error:
        await review_pdf(model, blank_pdf(1), "system", "prompt", Review)

    assert error.value.status_code == status_code
