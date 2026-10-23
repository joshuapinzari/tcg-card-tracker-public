from contextlib import nullcontext

import pandas as pd
import pytest

from database import opportunity_scanner
from services.analysis.opportunity_snapshot import Snapshot
from services.pipelines import refresh_opportunity_snapshot as pipeline


def wire(monkeypatch,tmp_path,frame=None):
    calls=[]
    monkeypatch.setattr(pipeline,"CACHE_DIR",tmp_path)
    monkeypatch.setattr(pipeline,"input_lock",lambda **kwargs:nullcontext())
    monkeypatch.setattr(pipeline,"load_snapshot",lambda:Snapshot(pd.DataFrame(),{},"missing"))
    monkeypatch.setattr(pipeline,"revision",lambda:"revision")
    monkeypatch.setattr(opportunity_scanner,"history_summary",lambda:{"rows":1000,"context_ready":True})
    def scan(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{"product_id":1,"printing":"Normal","current_price":20.0}]) if frame is None else frame
    monkeypatch.setattr(opportunity_scanner,"scan_raw_opportunities",scan)
    def save(frame,**kwargs):
        return Snapshot(frame,{**kwargs["metadata"],"cards":len(frame),"built_at":"test"},"ready")
    monkeypatch.setattr(pipeline,"save_snapshot",save)
    return calls


def test_build_uses_fixed_full_research_universe_not_ui_price_floor(monkeypatch,tmp_path):
    calls=wire(monkeypatch,tmp_path)
    result=pipeline.refresh_snapshot()
    assert result["cards"]==1
    assert calls==[{"price_floor_usd":5.0,"max_analogs":150}]


def test_if_needed_keeps_fresh_snapshot_without_scanning(monkeypatch,tmp_path):
    calls=wire(monkeypatch,tmp_path)
    monkeypatch.setattr(pipeline,"load_snapshot",lambda:Snapshot(pd.DataFrame(),{"cards":2},"ready"))
    assert pipeline.refresh_snapshot(only_if_needed=True)=={"cards":2}
    assert calls==[]


def test_missing_research_never_overwrites_saved_data(monkeypatch,tmp_path):
    calls=wire(monkeypatch,tmp_path)
    monkeypatch.setattr(opportunity_scanner,"history_summary",lambda:{"rows":0,"context_ready":False})
    monkeypatch.setattr(pipeline,"save_snapshot",lambda *a,**k:pytest.fail("must not save"))
    with pytest.raises(RuntimeError,match="missing/incomplete"):
        pipeline.refresh_snapshot()
    assert calls==[]


def test_empty_scan_keeps_previous_data(monkeypatch,tmp_path):
    wire(monkeypatch,tmp_path,frame=pd.DataFrame())
    monkeypatch.setattr(pipeline,"save_snapshot",lambda *a,**k:pytest.fail("must not save"))
    with pytest.raises(RuntimeError,match="no cards"):
        pipeline.refresh_snapshot()
