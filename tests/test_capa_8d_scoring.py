from app.features.capa_8d.schemas import Review, SlideReview, SummaryDraft
from app.features.capa_8d.scoring import build_analysis


def slide(number, discipline, verdict):
    return SlideReview(
        slideNumber=number, title=f"Slide {number}", discipline=discipline, verdict=verdict,
        logic="na" if verdict == "unrelated" else "sound", assessment="", findings=[], suggestions=[],
    )


def review(slides):
    return Review(
        headline="Headline",
        slides=slides,
        summary=SummaryDraft(overview="Overview", strengths=[], gaps=[], nextActions=[]),
    )


def test_full_coverage_scores_the_average_verdict():
    slides = [slide(i, f"D{i}", "good") for i in range(1, 9)]

    analysis = build_analysis(review(slides))

    assert analysis.score == 92
    assert all(d.status == "good" for d in analysis.summary.disciplines)


def test_missing_disciplines_are_penalised_and_reported():
    slides = [slide(1, None, "unrelated"), slide(2, "D1", "good"), slide(3, "D2", "bad")]

    analysis = build_analysis(review(slides))

    # average of good (92) and bad (30) is 61, minus 6 missing disciplines x 3
    assert analysis.score == 43
    statuses = {d.code: d.status for d in analysis.summary.disciplines}
    assert statuses["D1"] == "good"
    assert statuses["D2"] == "bad"
    assert statuses["D8"] == "missing"


def test_best_verdict_wins_for_a_discipline():
    slides = [slide(1, "D4", "bad"), slide(2, "D4", "warning")]

    analysis = build_analysis(review(slides))

    assert {d.code: d.status for d in analysis.summary.disciplines}["D4"] == "warning"


def test_only_unrelated_slides_score_zero():
    analysis = build_analysis(review([slide(1, None, "unrelated")]))

    assert analysis.score == 0


def test_slides_are_returned_in_order():
    analysis = build_analysis(review([slide(2, "D1", "good"), slide(1, None, "unrelated")]))

    assert [s.slideNumber for s in analysis.slides] == [1, 2]
