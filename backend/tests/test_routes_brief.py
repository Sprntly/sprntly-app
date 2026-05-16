"""Tests for /v1/brief routes — covers the dataset-required default change."""
from __future__ import annotations


def test_dataset_query_param_now_required(app_client):
    # Old API allowed dataset default 'asurion'. Now it must be passed.
    r = app_client.get("/v1/brief/status")
    assert r.status_code == 422  # FastAPI validation error


def test_status_unknown_dataset_is_empty(app_client):
    r = app_client.get("/v1/brief/status?dataset=ghost")
    assert r.status_code == 200
    body = r.json()
    assert body["dataset"] == "ghost"
    assert body["status"] == "empty"


def test_current_404_when_no_brief(app_client):
    r = app_client.get("/v1/brief/current?dataset=ghost")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["message"] == "No brief generated yet"


def test_current_returns_saved_brief(app_client, isolated_settings):
    db = isolated_settings["db"]
    brief_id = db.save_brief("acme", "Week 1", {"insights": []}, schema_version=1)
    r = app_client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == brief_id


def test_brief_routes_require_auth(unauth_client):
    r = unauth_client.get("/v1/brief/status?dataset=acme")
    assert r.status_code == 401
    r = unauth_client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 401
