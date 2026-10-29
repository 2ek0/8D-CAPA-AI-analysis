import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from app.config import Settings, get_settings
from app.errors import AppError
from app.features.capa_8d.schemas import CapaAnalysis
from app.features.capa_8d.service import CapaAnalyser, Event, get_capa_analyser
from app.security import require_service_key
from app.services.documents import validate_upload

logger = logging.getLogger(__name__)

# The portal page calls ResolveAIUrl("~/api/kat/8dcapa/analyse")
router = APIRouter(prefix="/api/kat/8dcapa", tags=["8D / CAPA"], dependencies=[Depends(require_service_key)])

# The test page has no key check: it holds no data, and its requests to /analyse send the key
page_router = APIRouter(prefix="/kat/8dcapa", include_in_schema=False)

LIVE_PAGE = Path(__file__).with_name("live.html")


@router.post(
    "/analyse",
    response_model=CapaAnalysis,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def analyse(
    file: UploadFile = File(...),
    accept: str = Header(default=""),
    analyser: CapaAnalyser = Depends(get_capa_analyser),
    settings: Settings = Depends(get_settings),
) -> CapaAnalysis | StreamingResponse:
    """Reviews an 8D report deck (.pptx or .pdf) slide by slide.

    With `Accept: text/event-stream` the review is sent as Server-Sent Events while it is written: `start`
    ({ slideCount }), `progress` ({ slideNumber, message }) while a slide is being reviewed, one `slide` per slide
    as soon as it is finished, then `summary` (the same body as the plain response) or `error` ({ "Message" }).
    Otherwise the whole analysis is returned as JSON at the end."""
    data = await file.read()
    filename = file.filename or ""
    validate_upload(filename, len(data), settings.max_upload_mb)

    if "text/event-stream" not in accept:
        return await analyser.analyse(filename, data)

    return StreamingResponse(
        server_sent_events(analyser.analyse_live(filename, data)),
        media_type="text/event-stream",
        # Stop proxies from holding events back until the response ends
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@page_router.get("/live")
async def live_page() -> HTMLResponse:
    """A test page that shows each slide's review as it arrives."""
    return HTMLResponse(LIVE_PAGE.read_text(encoding="utf-8"))


async def server_sent_events(events: AsyncIterator[Event]) -> AsyncIterator[str]:
    # The response has already started, so errors are sent as an event instead of an HTTP status
    try:
        async for name, data in events:
            yield f"event: {name}\ndata: {json.dumps(data)}\n\n"
    except AppError as error:
        yield f"event: error\ndata: {json.dumps({'Message': error.message})}\n\n"
    except Exception:
        logger.exception("Live 8D review failed")
        yield f"event: error\ndata: {json.dumps({'Message': 'The analysis failed, please try again.'})}\n\n"
