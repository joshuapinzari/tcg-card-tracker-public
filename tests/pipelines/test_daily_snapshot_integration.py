"""Daily integration control flow; all database/provider operations are mocked."""
from contextlib import nullcontext

from services.pipelines import daily_update as daily


def setup(monkeypatch):
    events = []
    monkeypatch.setattr(daily, "configure_logging", lambda: None)
    monkeypatch.setattr(daily, "initialise_update_pipeline_schema", lambda: None)
    monkeypatch.setattr(daily, "start_pipeline_run", lambda **kwargs: 123)
    for name in ("mark_component_started", "mark_component_finished", "mark_component_skipped", "finish_pipeline_run"):
        monkeypatch.setattr(daily, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(daily, "market_connect", lambda: nullcontext(object()))

    def raw(**kwargs):
        events.append("raw")
        return {"inserted": 2, "failed_groups": 0, "requests": 1}

    def primary(connection):
        events.append("primary")
        return 50

    def research():
        events.append("research")
        return {"index_rows": 100, "history_rows": 250, "products": 50}

    def snapshot(**kwargs):
        events.append(("snapshot", kwargs))
        return {"cards": 50, "built_at": "test"}

    monkeypatch.setattr(daily, "run_raw_daily_update", raw)
    monkeypatch.setattr(daily, "rebuild_primary_printings", primary)
    monkeypatch.setattr(daily, "_run_research_refresh", research)
    monkeypatch.setattr(daily, "refresh_snapshot", snapshot)
    return events


def run(**overrides):
    options = dict(dry_run=False, force=False, skip_catalog=True, skip_graded=True,
                   skip_research=False, raw_delay=0.65, raw_limit_groups=None,
                   graded_days=14, graded_max_data_points=14, graded_request_interval=1.1,
                   graded_retry_wait=65, graded_max_attempts=4, graded_credit_budget=500,
                   graded_limit=None)
    options.update(overrides)
    return daily.run_pipeline(**options)


def fail(*args, **kwargs):
    raise RuntimeError("simulated failure")


def test_snapshot_runs_after_completed_research(monkeypatch):
    events = setup(monkeypatch)
    result = run()
    assert events == ["raw", "primary", "research", ("snapshot", {"only_if_needed": True})]
    assert result["status"] == "success"
    assert result["opportunity_snapshot"]["cards"] == 50


def test_skipped_research_does_not_publish_a_snapshot(monkeypatch):
    events = setup(monkeypatch)
    result = run(skip_research=True)
    assert events == ["raw", "primary"]
    assert result["opportunity_snapshot"] is None


def test_partial_raw_update_does_not_publish_a_snapshot(monkeypatch):
    events = setup(monkeypatch)
    monkeypatch.setattr(daily, "run_raw_daily_update", lambda **kwargs: {"inserted": 2, "failed_groups": 1})
    result = run()
    assert events == []
    assert result["opportunity_snapshot"] is None
    assert result["failures"] == ["raw"]


def test_failed_research_does_not_publish_a_snapshot(monkeypatch):
    events = setup(monkeypatch)
    monkeypatch.setattr(daily, "_run_research_refresh", fail)
    result = run()
    assert events == ["raw", "primary"]
    assert result["opportunity_snapshot"] is None
    assert result["failures"] == ["research"]


def test_failed_primary_refresh_blocks_snapshot_even_after_raw_writes(monkeypatch):
    events = setup(monkeypatch)
    monkeypatch.setattr(daily, "rebuild_primary_printings", fail)
    result = run()
    assert result["opportunity_snapshot"] is None
    assert "raw" in result["failures"]
    assert not any(isinstance(event, tuple) and event[0] == "snapshot" for event in events)


def test_snapshot_failure_keeps_completed_data_update_and_reports_failure(monkeypatch):
    events = setup(monkeypatch)
    monkeypatch.setattr(daily, "refresh_snapshot", fail)
    result = run()
    assert events == ["raw", "primary", "research"]
    assert result["raw"]["inserted"] == 2
    assert result["research"]["history_rows"] == 250
    assert result["status"] == "failed"
    assert result["failures"] == ["opportunity_snapshot"]


def test_no_new_prices_checks_saved_snapshot_without_rebuilding_research(monkeypatch):
    events = setup(monkeypatch)
    monkeypatch.setattr(daily, "run_raw_daily_update", lambda **kwargs: {"inserted": 0, "failed_groups": 0})
    result = run()
    assert events == ["primary", ("snapshot", {"only_if_needed": True})]
    assert result["status"] == "success"
