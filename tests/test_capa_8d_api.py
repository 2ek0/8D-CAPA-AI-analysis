from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import Settings, get_settings
from app.features.capa_8d.schemas import Review, SlideReview, SummaryDraft
from app.features.capa_8d.scoring import build_analysis
from app.features.capa_8d.service import get_capa_analyser
from app.main import app


class FakeAnalyser:
    """Stands in for Claude so the API can be tested without calling it."""

    async def analyse(self, filename, data):
        return build_analysis(Review(
            headline="Solid structure",
            slides=[SlideReview(
                slideNumber=1, title="Team", discipline="D1", verdict="good", logic="sound",
                assessment="Clear.", findings=[], suggestions=[],
            )],
            summary=SummaryDraft(overview="Overview", strengths=[], gaps=[], nextActions=[]),
        ))


@pytest.fixture
def client():
    app.dependency_overrides[get_capa_analyser] = lambda: FakeAnalyser()
    app.dependency_overrides[get_settings] = lambda: Settings(service_key="secret", max_upload_mb=1, _env_file=None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=720, height=405)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def post(client, filename, data, key="secret"):
    return client.post(
        "/api/kat/8dcapa/analyse",
        files={"file": (filename, data, "application/octet-stream")},
        headers={"X-Service-Key": key} if key else {},
    )


def test_returns_the_analysis(client):
    response = post(client, "report.pdf", pdf_bytes())

    assert response.status_code == 200
    body = response.json()
    assert body["headline"] == "Solid structure"
    assert body["slides"][0]["verdict"] == "good"
    assert len(body["summary"]["disciplines"]) == 8
    assert isinstance(body["score"], int)


def test_rejects_a_missing_service_key(client):
    response = post(client, "report.pdf", pdf_bytes(), key=None)

    assert response.status_code == 401
    assert response.json() == {"Message": "Unauthorised."}


def test_rejects_unsupported_files(client):
    response = post(client, "report.docx", b"data")

    assert response.status_code == 400
    assert "Message" in response.json()


def test_rejects_files_over_the_size_limit(client):
    response = post(client, "report.pdf", b"x" * (1024 * 1024 + 1))

    assert response.status_code == 413


def test_rejects_a_request_without_a_file(client):
    response = client.post("/api/kat/8dcapa/analyse", headers={"X-Service-Key": "secret"})

    assert response.status_code == 400
    assert "Message" in response.json()


def test_allows_the_listed_portal_origins_with_cookies(monkeypatch):
    from app.main import create_app

    monkeypatch.setattr("app.main.get_settings", lambda: Settings(cors_origins="http://portal.test", _env_file=None))
    response = TestClient(create_app()).options("/api/kat/8dcapa/analyse", headers={
        "Origin": "http://portal.test", "Access-Control-Request-Method": "POST",
    })

    assert response.headers["access-control-allow-origin"] == "http://portal.test"
    assert response.headers["access-control-allow-credentials"] == "true"
