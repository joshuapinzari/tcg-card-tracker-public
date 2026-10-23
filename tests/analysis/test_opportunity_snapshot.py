"""Persistence tests use synthetic records and temporary files, never user data."""
from datetime import datetime, timedelta, timezone
import gzip
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services.analysis import opportunity_snapshot as cache

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def sample():
    return pd.DataFrame([
        {"product_id": 1, "printing": "Normal", "current_price": 20.0,
         "research_score": 67.12345678901234, "expected_return_90d": np.nan,
         "setups": ["HEADING_UP"], "research_factors": [{"factor": "x", "contribution": 1.25}],
         "analysis_date": pd.Timestamp("2026-10-19")},
        {"product_id": 2, "printing": "Holofoil", "current_price": 70.0,
         "research_score": 55.0, "expected_return_90d": 0.12, "setups": [],
         "research_factors": [], "analysis_date": pd.Timestamp("2026-10-19")},
    ])


def save(tmp_path, frame=None, **kwargs):
    return cache.save_snapshot(sample() if frame is None else frame, cache_dir=tmp_path,
                               fingerprint="test", source_revision=cache.revision(tmp_path),
                               metadata={"max_analogs": 150}, now=NOW, **kwargs)


def test_cache_round_trip_keeps_precise_scores_and_nested_factors(tmp_path):
    saved = save(tmp_path)
    assert saved.status == "ready"
    pd.testing.assert_frame_equal(saved.frame, sample())
    assert saved.card(1, "Normal")["research_score"] == 67.12345678901234
    assert saved.card(1, "Normal")["research_factors"] == [{"factor": "x", "contribution": 1.25}]


@pytest.mark.parametrize("printing", [None, "", "Reverse Holofoil", "Holofoil"])
def test_same_product_never_borrows_another_finish_score(tmp_path, printing):
    saved = save(tmp_path)
    assert saved.card(1, printing) is None


def test_missing_cache_does_not_require_database_or_scan(tmp_path):
    result = cache.load_snapshot(cache_dir=tmp_path, fingerprint="test", now=NOW)
    assert result.status == "missing"
    assert result.frame.empty
    assert not tmp_path.joinpath("opportunities-v1.json.gz").exists()


@pytest.mark.parametrize("content", [b"not gzip", gzip.compress(b"{}"), gzip.compress(b'{"metadata":null}')])
def test_corrupt_cache_does_not_trigger_a_rebuild(tmp_path, content):
    cache.snapshot_path(tmp_path).write_bytes(content)
    result = cache.load_snapshot(cache_dir=tmp_path, fingerprint="test", now=NOW)
    assert result.status == "invalid"
    assert result.frame.empty


def test_model_change_blocks_old_research(tmp_path):
    save(tmp_path)
    result = cache.load_snapshot(cache_dir=tmp_path, fingerprint="new model", now=NOW)
    assert result.status == "incompatible"
    assert result.card(1, "Normal") is None


def test_input_change_marks_old_display_stale_and_blocks_card_analysis(tmp_path):
    save(tmp_path)
    cache.invalidate_snapshot("same-day RAW correction", cache_dir=tmp_path)
    result = cache.load_snapshot(cache_dir=tmp_path, fingerprint="test", now=NOW)
    assert result.status == "stale"
    assert not result.frame.empty  # can still be displayed with an explicit warning
    assert result.card(1, "Normal") is None


def test_age_limit_does_not_depend_on_streamlit_ttl(tmp_path):
    save(tmp_path)
    result = cache.load_snapshot(cache_dir=tmp_path, fingerprint="test", now=NOW + timedelta(hours=27))
    assert result.status == "stale"
    assert result.card(1, "Normal") is None


def test_rebuild_publishes_new_revision(tmp_path):
    save(tmp_path)
    cache.invalidate_snapshot("updated", cache_dir=tmp_path)
    result = save(tmp_path)
    assert result.status == "ready"
    assert result.card(1, "Normal") is not None


def test_racing_input_update_keeps_previous_snapshot(tmp_path):
    save(tmp_path)
    previous = cache.snapshot_path(tmp_path).read_bytes()
    before = cache.revision(tmp_path)
    cache.invalidate_snapshot("writer ran", cache_dir=tmp_path)
    with pytest.raises(RuntimeError, match="Inputs changed"):
        cache.save_snapshot(sample(), cache_dir=tmp_path, source_revision=before,
                            fingerprint="test", metadata={}, now=NOW)
    assert cache.snapshot_path(tmp_path).read_bytes() == previous


def test_atomic_publish_failure_keeps_previous_snapshot(tmp_path, monkeypatch):
    save(tmp_path)
    previous = cache.snapshot_path(tmp_path).read_bytes()
    monkeypatch.setattr(cache.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        save(tmp_path)
    assert cache.snapshot_path(tmp_path).read_bytes() == previous
    assert not list(tmp_path.glob(".snapshot-*"))


def test_snapshot_reopens_in_another_python_process(tmp_path):
    save(tmp_path)
    code = (
        "from pathlib import Path; from datetime import datetime, timezone; "
        "from services.analysis.opportunity_snapshot import load_snapshot; "
        f"s=load_snapshot(cache_dir=Path({str(tmp_path)!r}), fingerprint='test', "
        "now=datetime(2026,10,19,12,tzinfo=timezone.utc)); "
        "assert s.status=='ready'; assert s.card(1,'Normal')['current_price']==20; "
        "assert s.card(1,'Reverse Holofoil') is None"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_input_lock_is_reentrant_and_released_after_error(tmp_path):
    with pytest.raises(ValueError):
        with cache.input_lock(cache_dir=tmp_path):
            with cache.input_lock(cache_dir=tmp_path, blocking=False):
                raise ValueError("simulated crash")
    with cache.input_lock(cache_dir=tmp_path, blocking=False):
        pass


def test_other_process_cannot_build_while_input_writer_is_active(tmp_path):
    code = (
        "from pathlib import Path; from services.analysis.opportunity_snapshot import input_lock; "
        f"exec(\"try:\\n with input_lock(cache_dir=Path({str(tmp_path)!r}), blocking=False): pass"
        "\\nexcept RuntimeError: raise SystemExit(4)\")"
    )
    with cache.input_lock(cache_dir=tmp_path):
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 4, result.stderr


def test_write_decorator_invalidates_even_after_failure(monkeypatch):
    from contextlib import nullcontext
    calls = []
    monkeypatch.setattr(cache, "input_lock", lambda **kwargs: nullcontext())
    monkeypatch.setattr(cache, "invalidate_snapshot", calls.append)
    @cache.snapshot_input_writer
    def failed():
        raise RuntimeError("failed provider")
    with pytest.raises(RuntimeError):
        failed()
    assert len(calls) == 2
    assert calls[0].endswith("starting") and calls[1].endswith("finished")


def test_database_namespace_changes_for_a_different_database(monkeypatch):
    old = cache._database_namespace()
    monkeypatch.setenv("TCG_DATABASE_NAME", "different_test_database")
    assert cache._database_namespace() != old


def test_json_contains_no_nonstandard_nan_literals(tmp_path):
    save(tmp_path)
    with gzip.open(cache.snapshot_path(tmp_path), "rt") as source:
        text = source.read()
    assert "NaN" not in text and "Infinity" not in text
    json.loads(text)
