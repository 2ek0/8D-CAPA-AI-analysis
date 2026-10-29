import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.clients.base import get_model_client
from app.config import Settings, get_settings
from app.errors import AppError
from app.features.capa_8d.service import CapaAnalyser, current_activity, finished_slides
from app.main import app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def slide(number):
    return {
        "slideNumber": number, "title": f"Slide {number}", "discipline": "D1", "verdict": "good", "logic": "sound",
        "assessment": "Clear.", "findings": [], "suggestions": [],
    }


REVIEW = json.dumps({
    "headline": "Solid",
    "slides": [slide(1), slide(2), slide(3)],
    "summary": {"overview": "Ok", "strengths": [], "gaps": [], "nextActions": []},
})


def pdf_bytes(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=720, height=405)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class StreamingModel:
    """Writes the review a few characters at a time, recording how much was written when each event left."""

    def __init__(self, text, error=None):
        self.text = text
        self.error = error
        self.written = 0

    async def stream_pdf(self, pdf, system, prompt, output):
        for i in range(0, len(self.text), 5):
            self.written = i + 5
            yield self.text[i:i + 5]
        if self.error:
            raise self.error


def test_a_slide_counts_as_finished_once_the_next_one_starts():
    second_slide_started = REVIEW[:REVIEW.index('"slideNumber": 2') + 5]

    assert [s.slideNumber for s in finished_slides(second_slide_started)] == [1]


def test_the_last_slide_counts_as_finished_once_the_summary_starts():
    summary_started = REVIEW[:REVIEW.index('"summary"') + 12]

    assert [s.slideNumber for s in finished_slides(summary_started)] == [1, 2, 3]


def test_the_activity_follows_the_field_being_written():
    second_slide = REVIEW.index('"slideNumber": 2')

    assert current_activity(REVIEW[:REVIEW.index('"assessment"', second_slide) + 16]) == (2, "Reading the slide")
    assert current_activity(REVIEW[:REVIEW.index('"findings"', second_slide) + 13]) == (2, "Checking evidence & data")
    assert current_activity(REVIEW[:REVIEW.index('"summary"') + 12]) is None


async def test_each_slide_is_sent_before_the_review_is_complete():
    model = StreamingModel(REVIEW)
    analyser = CapaAnalyser(model, Settings(_env_file=None))

    events = []
    async for name, data in analyser.analyse_live("report.pdf", pdf_bytes(3)):
        events.append((name, data, model.written))

    names = [name for name, _, _ in events]
    assert names[0] == "start" and events[0][1] == {"slideCount": 3}
    assert [name for name in names if name != "progress"] == ["start", "slide", "slide", "slide", "summary"]
    assert [data["slideNumber"] for name, data, _ in events if name == "slide"] == [1, 2, 3]
    # each slide is announced as being worked on before it is sent
    for number in (1, 2, 3):
        progress = names.index("progress", next(i for i, e in enumerate(events) if e[0] == "progress" and e[1]["slideNumber"] == number))
        assert progress < next(i for i, e in enumerate(events) if e[0] == "slide" and e[1]["slideNumber"] == number)
    slides = [written for name, _, written in events if name == "slide"]
    assert slides[0] < len(REVIEW) / 2
    assert slides[-1] < len(REVIEW) - 20  # before the summary is finished
    summary = events[-1][1]
    assert isinstance(summary["score"], int) and summary["headline"] == "Solid" and len(summary["summary"]["disciplines"]) == 8


@pytest.fixture
def client():
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def events_of(response):
    return [
        (block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
        for block in response.text.strip().split("\n\n")
    ]


def post_streaming(client):
    return client.post(
        "/api/kat/8dcapa/analyse",
        files={"file": ("report.pdf", pdf_bytes(3))},
        headers={"Accept": "text/event-stream"},
    )


def test_streams_server_sent_events_when_asked(client):
    app.dependency_overrides[get_model_client] = lambda: StreamingModel(REVIEW)

    response = post_streaming(client)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    names = [name for name, _ in events_of(response) if name != "progress"]
    assert names == ["start", "slide", "slide", "slide", "summary"]


def test_returns_plain_json_otherwise(client):
    app.dependency_overrides[get_model_client] = lambda: StreamingModel(REVIEW)

    response = client.post("/api/kat/8dcapa/analyse", files={"file": ("report.pdf", pdf_bytes(3))})

    assert response.headers["content-type"].startswith("application/json")
    assert len(response.json()["slides"]) == 3


def test_stream_sends_errors_as_an_event(client):
    app.dependency_overrides[get_model_client] = lambda: StreamingModel(REVIEW[:40], AppError(503, "KAT is busy."))

    response = post_streaming(client)

    assert events_of(response)[-1] == ("error", {"Message": "KAT is busy."})


def test_live_page_is_served(client):
    response = client.get("/kat/8dcapa/live")

    assert response.status_code == 200
    assert "/api/kat/8dcapa/analyse" in response.text
