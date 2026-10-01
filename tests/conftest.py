import os, sys, pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # main.py serves ./static


@pytest.fixture(autouse=True)
def oc_env(monkeypatch):
    from scraper import opencorporates as oc, gleif, companieshouse
    monkeypatch.setenv("OPENCORPORATES_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENCORPORATES_MIN_INTERVAL", "0")
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    for api in (gleif.API, companieshouse.API):
        monkeypatch.setattr(api, "min_interval", 0); api.cache.clear()
    monkeypatch.setenv("GAPFILL_NOMINATIM", "0")      # tests never touch the network; gap-fill tests turn it on with mocked responses
    monkeypatch.setenv("NOMINATIM_MIN_INTERVAL", "0")
    from scraper import gapfill
    gapfill._cache.clear()
    oc.clear_cache()
    yield
    oc.clear_cache()
