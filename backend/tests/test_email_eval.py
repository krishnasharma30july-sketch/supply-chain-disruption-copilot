"""The AI-versus-template email comparison table (pure formatting; the drafting needs a real model)."""

from app.evals.suite import format_email_quality


def test_table_shows_both_writers_with_counts_and_averages():
    template = {"n": 10, "passed": 10, "warmth": [3, 3, 4], "clarity": [4, 4, 4], "words": [100, 120]}
    ai = {"n": 10, "passed": 7, "warmth": [4, 5], "clarity": [5, 4], "words": [150]}
    md = "\n".join(format_email_quality(template, ai, "openai/gpt-oss-120b", ["Sample: 10 customers."]))
    assert "## Email quality: AI-written against templates" in md
    assert "| AI (openai/gpt-oss-120b) | 10 | 7/10 | 4.5 (2 rated) | 4.5 | 150.0 |" in md
    assert "| Template | 10 | 10/10 | 3.3 (3 rated) | 4.0 | 110.0 |" in md
    assert "- Sample: 10 customers." in md


def test_table_handles_a_side_with_no_ratings():
    empty = {"n": 5, "passed": 0, "warmth": [], "clarity": [], "words": []}
    md = "\n".join(format_email_quality(empty, empty, None, []))
    assert "| AI (n/a) | 5 | 0/5 | n/a (0 rated) | n/a | n/a |" in md
