"""The CLI prints the impact summary and one sample email (the brief asks for both), not a truncated report."""

from app.cli import console_report

REPORT = """# Report

## Heavy rains impact 681 orders

## Key figures
- delayed: 681

### Recommendations
- call the critical customers

## Top 25 orders by severity

| order | severity |
|---|---|
| BC9C2FB1 | 81 |

## Sample customer emails

### To Debora (orders BC9C2FB1): template, QA passed

Subject: Update on your order

Hello Debora, your new window is Aug 15 to Aug 23.

### To Bianca (orders A98F7931): template, QA passed

Subject: Update on your other order

## Assumptions
- A1: simulation clock
"""


def test_console_report_has_the_summary_and_the_first_email_only():
    out = console_report(REPORT)
    assert "Heavy rains impact 681 orders" in out and "Key figures" in out and "Recommendations" in out
    assert "To Debora" in out and "Hello Debora" in out
    assert "To Bianca" not in out  # one sample is enough on screen
    assert "| BC9C2FB1 | 81 |" not in out  # the order table stays in the files
    assert "Assumptions" not in out


def test_console_report_without_emails_still_prints_the_summary():
    short = "# Report\n\n## Key figures\n- delayed: 3\n"
    out = console_report(short)
    assert "Key figures" in out and "Sample customer emails" not in out
