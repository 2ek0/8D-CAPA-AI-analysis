import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends
from pydantic import ValidationError
from pydantic_core import from_json

from app.clients.base import ModelClient, get_model_client, parse, review_pdf
from app.config import Settings, get_settings
from app.features.capa_8d.prompt import SYSTEM_PROMPT, user_prompt
from app.features.capa_8d.schemas import CapaAnalysis, Review, SlideReview
from app.features.capa_8d.scoring import build_analysis
from app.services.documents import count_pages, to_pdf

logger = logging.getLogger(__name__)

# (event name, data) sent to the live page as each step finishes
Event = tuple[str, dict[str, Any]]


class CapaAnalyser:
    def __init__(self, model: ModelClient, settings: Settings):
        self.model = model
        self.settings = settings

    async def analyse(self, filename: str, data: bytes) -> CapaAnalysis:
        pdf = await to_pdf(filename, data, self.settings.soffice_path, self.settings.conversion_timeout_seconds)
        page_count = count_pages(pdf)
        review = await review_pdf(self.model, pdf, SYSTEM_PROMPT, user_prompt(page_count), Review)
        return self._finish(review, page_count)

    async def analyse_live(self, filename: str, data: bytes) -> AsyncIterator[Event]:
        """Like analyse, but streamed the way the portal page reads it (see requestCAPAAiAnalysis): `start`,
        `progress` while a slide is being written, each `slide` as soon as it is finished, then `summary`."""
        pdf = await to_pdf(filename, data, self.settings.soffice_path, self.settings.conversion_timeout_seconds)
        page_count = count_pages(pdf)
        yield "start", {"slideCount": page_count}

        text = ""
        sent = 0
        activity = None
        async for chunk in self.model.stream_pdf(pdf, SYSTEM_PROMPT, user_prompt(page_count), Review):
            text += chunk
            # Only worth checking when a new field or object starts
            if '"' not in chunk and "{" not in chunk:
                continue

            for slide in finished_slides(text)[sent:]:
                sent += 1
                yield "slide", slide.model_dump()

            current = current_activity(text)
            if current and current != activity:
                activity = current
                yield "progress", {"slideNumber": current[0], "message": current[1]}

        review = parse(Review, text)
        for slide in review.slides[sent:]:
            yield "slide", slide.model_dump()
        yield "summary", self._finish(review, page_count).model_dump()

    def _finish(self, review: Review, page_count: int) -> CapaAnalysis:
        if len(review.slides) != page_count:
            logger.warning("8D review returned %d slides for a %d page deck", len(review.slides), page_count)
        return build_analysis(review)


def finished_slides(text: str) -> list[SlideReview]:
    """The slides fully written so far in a partial review. The last slide in the list may still be being written,
    so it only counts once the model has moved on to the summary."""
    partial = from_json(text, allow_partial=True)
    if not isinstance(partial, dict) or not isinstance(partial.get("slides"), list):
        return []

    slides = partial["slides"] if "summary" in partial else partial["slides"][:-1]
    finished = []
    for slide in slides:
        try:
            finished.append(SlideReview.model_validate(slide))
        except ValidationError:
            break
    return finished


# What the page shows while a slide is being written, by the field the model is on
ACTIVITIES = {
    "assessment": "Reading the slide",
    "findings": "Checking evidence & data",
    "suggestions": "Drafting suggestions",
}


def current_activity(text: str) -> tuple[int, str] | None:
    """(slide number, activity) of the slide the model is writing now, or None between slides."""
    partial = from_json(text, allow_partial=True)
    if not isinstance(partial, dict) or "summary" in partial or not partial.get("slides"):
        return None

    slide = partial["slides"][-1]
    if not isinstance(slide, dict):
        return None

    number = slide.get("slideNumber") if isinstance(slide.get("slideNumber"), int) else len(partial["slides"])
    field = next((name for name in reversed(list(slide)) if name in ACTIVITIES), "assessment")
    return number, ACTIVITIES[field]


def get_capa_analyser(
    model: ModelClient = Depends(get_model_client),
    settings: Settings = Depends(get_settings),
) -> CapaAnalyser:
    return CapaAnalyser(model, settings)
