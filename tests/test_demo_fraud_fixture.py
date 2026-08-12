"""Offline DEMO-FRAUD fixture — no Zefix/Moneyhouse."""

from __future__ import annotations

import pytest

from app.hr_network import demo_fixture as df
from app.hr_network.demo_fixture import (
    DemoFixtureError,
    build_demo_fraud_network,
    build_demo_hr_network,
    demo_search_hits,
    is_demo_request,
    resolve_fixture_path,
    usable_company_query,
)


def test_fixture_lives_under_app_not_data_volume():
    path = resolve_fixture_path()
    assert path.is_file()
    assert path.parts[-3:] == ("hr_network", "fixtures", "demo_fraud_firm.json")


def test_usable_company_query_rejects_punctuation():
    assert not usable_company_query(company="?")
    assert not usable_company_query(company="??")
    assert not usable_company_query(company="")
    assert usable_company_query(company="AB")
    assert usable_company_query(uid="CHE-1")
    assert usable_company_query(company="foto fiest")


def test_missing_fixture_raises_demo_error(tmp_path, monkeypatch):
    df.reload_fixture()
    monkeypatch.setattr(df, "_FIXTURE_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(df, "_PACKAGE_FIXTURE", tmp_path / "missing.json")
    monkeypatch.setattr(df, "_DATA_FIXTURE", tmp_path / "also_missing.json")
    with pytest.raises(DemoFixtureError):
        df._raw_fixture()
    df.reload_fixture()


def test_is_demo_request_by_uid_and_name():
    assert is_demo_request(uid="CHE-000.000.001")
    assert is_demo_request(uid="CHE000000001")
    assert is_demo_request(name="DEMO-FRAUD GmbH")
    assert is_demo_request(name="demo-fraud")
    assert is_demo_request(demo="fraud")
    assert not is_demo_request(name="Acme AG")
    assert not is_demo_request(uid="CHE-123.456.789")


def test_demo_search_hits():
    hits = demo_search_hits("DEMO-FRAUD")
    assert len(hits) == 1
    assert hits[0]["uid"] == "CHE-000.000.001"
    assert hits[0].get("demo_only") is True
    assert demo_search_hits("xyz-unknown") == []


def test_hr_and_level_subsets():
    hr = build_demo_hr_network()
    assert hr["demo_only"] is True
    assert hr["company"]["uid"] == "CHE-000.000.001"
    assert any("Neueintragung" in w for w in hr["warnings"])
    assert hr["level"] == 2
    l2_ids = {n["id"] for n in hr["nodes"]}
    assert "company:9000001" in l2_ids
    assert "person:max-muster" in l2_ids
    assert "company:9000004" not in l2_ids  # L3 mandate firm

    l3 = build_demo_fraud_network(level=3)
    l3_ids = {n["id"] for n in l3["nodes"]}
    assert "company:9000004" in l3_ids
    assert "company:9000005" not in l3_ids  # L4

    l5 = build_demo_fraud_network(level=5)
    assert len(l5["nodes"]) > len(l3["nodes"])
    assert any(n.get("label") == "Jonas Fiktiv" for n in l5["nodes"])
    assert l5["stats"]["person_search"]["method"] == "demo-fixture"
