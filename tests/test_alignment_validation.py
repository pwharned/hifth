from src.quran_alignment.validate import (
    ValidationIssue,
    ValidationReport,
    check_low_confidence_scores,
)


def word(score):
    return {
        "index": 0,
        "surah": 1,
        "ayah": 1,
        "text": "word",
        "score": score,
    }


def test_low_confidence_check_ignores_unavailable_scores():
    assert check_low_confidence_scores([word(None)]) == []
    assert check_low_confidence_scores([{k: v for k, v in word(None).items() if k != "score"}]) == []


def test_low_confidence_check_reports_available_low_score():
    issues = check_low_confidence_scores([word(0.25)])

    assert len(issues) == 1
    assert issues[0].check == "low_confidence"


def test_soft_warning_does_not_fail_report():
    report = ValidationReport(
        surah_number=1,
        total_words=1,
        issues=[ValidationIssue("silence_gap", 0, 1, 1, "word", "long gap")],
    )

    assert report.passed
    assert "PASSED" in report.summary()


def test_hard_issue_fails_report():
    report = ValidationReport(
        surah_number=1,
        total_words=1,
        issues=[ValidationIssue("negative_duration", 0, 1, 1, "word", "bad range")],
    )

    assert not report.passed
    assert "FAILED" in report.summary()
