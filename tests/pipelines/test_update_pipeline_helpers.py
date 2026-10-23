from datetime import date

from database.update_pipeline import freshness_label


def test_freshness_label_marks_current_and_stale_dates():
    today = date(2026, 10, 14)

    assert freshness_label(today, today=today) == "2026-10-14 · current"
    assert freshness_label(date(2026, 10, 13), today=today) == "2026-10-13 · 1 day old"
    assert freshness_label(date(2026, 10, 10), today=today) == "2026-10-10 · 4 days old"
    assert freshness_label(None, today=today) == "no data"
