"""Score and D1-D8 coverage, calculated in code so the same review always gets the same score.
Uses the same rules as generateCAPAMockAnalysis in the portal page."""
from app.features.capa_8d.schemas import CapaAnalysis, DisciplineStatus, Review, Summary

DISCIPLINES = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]
VERDICT_SCORE = {"good": 92, "warning": 64, "bad": 30}
VERDICT_RANK = {"bad": 1, "warning": 2, "good": 3}
MISSING_DISCIPLINE_PENALTY = 3


def discipline_coverage(review: Review) -> dict[str, str]:
    """The best verdict given to each discipline; disciplines with no assessed slide are left out."""
    coverage: dict[str, str] = {}

    for slide in review.slides:
        if slide.verdict == "unrelated" or not slide.discipline:
            continue
        best = coverage.get(slide.discipline)
        if best is None or VERDICT_RANK[slide.verdict] > VERDICT_RANK[best]:
            coverage[slide.discipline] = slide.verdict

    return coverage


def calculate_score(review: Review, coverage: dict[str, str]) -> int:
    assessed = [slide for slide in review.slides if slide.verdict != "unrelated"]
    if not assessed:
        return 0

    missing = [code for code in DISCIPLINES if code not in coverage]
    average = sum(VERDICT_SCORE[slide.verdict] for slide in assessed) / len(assessed)
    return max(5, min(98, round(average - len(missing) * MISSING_DISCIPLINE_PENALTY)))


def build_analysis(review: Review) -> CapaAnalysis:
    coverage = discipline_coverage(review)

    return CapaAnalysis(
        score=calculate_score(review, coverage),
        headline=review.headline,
        slides=sorted(review.slides, key=lambda slide: slide.slideNumber),
        summary=Summary(
            **review.summary.model_dump(),
            disciplines=[DisciplineStatus(code=code, status=coverage.get(code, "missing")) for code in DISCIPLINES],
        ),
    )
