"""The result contract of nxportal Scripts/Pages/KatAi/KatAiTool/8DCAPAAiAnalysis.js (see requestCAPAAiAnalysis).
Field names are camelCase because the page reads them as-is."""
from typing import Literal

from pydantic import BaseModel

Discipline = Literal["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]
Verdict = Literal["good", "warning", "bad", "unrelated"]


class Finding(BaseModel):
    type: Literal["strength", "issue", "error", "info"]
    text: str
    evidence: str | None  # quote or data taken from the slide


class Suggestion(BaseModel):
    priority: Literal["high", "medium", "low"]
    text: str


class SlideReview(BaseModel):
    slideNumber: int
    title: str
    discipline: Discipline | None
    verdict: Verdict
    logic: Literal["sound", "partial", "unsound", "na"]
    assessment: str
    findings: list[Finding]
    suggestions: list[Suggestion]


class SummaryDraft(BaseModel):
    overview: str
    strengths: list[str]
    gaps: list[str]
    nextActions: list[str]


class Review(BaseModel):
    """What the model returns. The score and discipline coverage are calculated in code (scoring.py)."""

    headline: str
    slides: list[SlideReview]
    summary: SummaryDraft


class DisciplineStatus(BaseModel):
    code: Discipline
    status: Literal["good", "warning", "bad", "missing"]


class Summary(SummaryDraft):
    disciplines: list[DisciplineStatus]


class CapaAnalysis(BaseModel):
    """What the page receives."""

    score: int
    headline: str
    slides: list[SlideReview]
    summary: Summary
