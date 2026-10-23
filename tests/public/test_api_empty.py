"""FastAPI default is safe with no database, credentials or provider access."""

from datetime import date
import sys
import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from api.main import _own_history, app, private_data_access_enabled


def test_api_returns_only_empty_market_results_by_default(monkeypatch):
    monkeypatch.delenv("TCG_DATA_MODE", raising=False)
    monkeypatch.delenv("TCG_API_DATA_ACCESS", raising=False)
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok", "data_access": False}
    assert client.get("/v1/cards?q=Vileplume").json() == []
    assert client.get("/v1/cards/88255/history?market=RAW&printing=Reverse%20Holofoil").json() == {
        "product_id": 88255, "observations": [], "data_available": False,
    }
    assert client.get("/v1/opportunities").json() == {"status": "no_data", "count": 0, "cards": []}
    assert client.get("/v1/cards?q=a").status_code == 422
    assert client.get("/v1/cards/88255/history?days=0").status_code == 422


@pytest.mark.parametrize("flags", [("local", "0"), ("empty", "1"), ("local", None)])
def test_private_api_requires_two_explicit_opt_ins(monkeypatch, flags):
    showcase, api = flags
    monkeypatch.setenv("TCG_DATA_MODE", showcase)
    if api is None:
        monkeypatch.delenv("TCG_API_DATA_ACCESS", raising=False)
    else:
        monkeypatch.setenv("TCG_API_DATA_ACCESS", api)
    assert private_data_access_enabled() is False


def test_raw_finish_history_stays_source_separated(monkeypatch):
    raw = types.ModuleType("database.canonical_cards")
    provider = types.ModuleType("database.provider_history")
    today = date.today()
    calls = []

    def raw_history(pid, printing):
        calls.append((pid, printing))
        return [(today, printing, 15.0)]

    raw.get_canonical_raw_history_rows = raw_history
    provider.get_product_provider_history = lambda pid: [
        (today, "RAW", 2.0, 1, 1.0, "raw"),
        (today, "PSA_9", 80.0, 1, 1.0, "graded"),
        (today, "PSA_10", 130.0, 1, 1.0, "graded"),
    ]
    monkeypatch.setitem(sys.modules, "database.canonical_cards", raw)
    monkeypatch.setitem(sys.modules, "database.provider_history", provider)
    response = _own_history(88255, market=None, printing="Reverse Holofoil", days=30)
    assert calls == [(88255, "Reverse Holofoil")]
    assert sorted(item.market_type for item in response) == ["PSA_10", "PSA_9", "RAW"]
    assert sum(item.market_type == "RAW" for item in response) == 1
    assert response[0].printing == "Reverse Holofoil" or any(
        item.market_type == "RAW" and item.printing == "Reverse Holofoil" for item in response
    )
    assert all(item.currency == "USD" for item in response)


@pytest.mark.parametrize('url', ['/v1/cards/0/history','/v1/cards/-1/history',
    '/v1/cards/1/history?market=UNKNOWN','/v1/opportunities?limit=251'])
def test_api_rejects_invalid_inputs(monkeypatch,url):
    monkeypatch.setenv('TCG_DATA_MODE','empty')
    assert TestClient(app).get(url).status_code==422


def test_local_api_requires_exact_finish_instead_of_falling_back(monkeypatch):
    monkeypatch.setenv('TCG_DATA_MODE','local')
    monkeypatch.setenv('TCG_API_DATA_ACCESS','1')
    client=TestClient(app)
    response=client.get('/v1/cards/1/history?market=RAW')
    assert response.status_code==422 and 'exact RAW' in response.json()['detail']


def test_api_empty_response_even_when_an_old_cache_is_present(monkeypatch):
    monkeypatch.setenv('TCG_DATA_MODE','empty')
    monkeypatch.setenv('TCG_API_DATA_ACCESS','1')
    import services.analysis.opportunity_snapshot as module
    def forbidden(*args,**kwargs): raise AssertionError('Must not inspect local cache')
    monkeypatch.setattr(module,'load_snapshot',forbidden)
    assert TestClient(app).get('/v1/opportunities').json()['cards']==[]
