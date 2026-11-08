"""Regression tests: explicit Telegram group choices cannot be bypassed."""
import pytest

from telegram_monitor.cloud.collector import active_entity_map
from telegram_monitor.cloud.source_registry import SourceRegistry


def _source(peer_id, *, public=True):
    return {
        "peer_id": peer_id,
        "title": f"Group {peer_id}",
        "username": f"group{peer_id}" if public else None,
        "kind": "megagroup",
        "visibility": "public" if public else "private",
    }


def test_registry_selection_survives_rescan_and_is_audited(tmp_path):
    registry = SourceRegistry(
        "sqlite:///" + str(tmp_path / "groups.db"), initialize=True
    )
    try:
        registry.record_scan([_source(11), _source(12)], auto_public=False)
        approved, blocked = registry.selection_ids()
        assert approved == set()
        assert blocked == set()

        assert registry.change_status(11, "approve") == "approved"
        assert registry.change_status(12, "disable") == "disabled"
        assert registry.selection_ids() == ({11}, {12})
        audit = registry.recent_audit()
        assert len(audit) == 2
        assert {entry["action"] for entry in audit} == {"approve", "disable"}

        # A disabled source must not become enabled on the next discovery pass.
        registry.record_scan([_source(11), _source(12)], auto_public=True)
        assert registry.selection_ids() == ({11}, {12})
        assert len(registry.recent_audit()) == 2

        assert registry.change_status(12, "approve") == "approved"
        assert registry.selection_ids() == ({11, 12}, set())
        assert len(registry.recent_audit()) == 3
    finally:
        registry.dispose()


def test_disabling_configured_sources_blocks_live_and_history():
    configured = {11: "configured group", 12: "configured disabled"}
    discovered = {13: "new approved", 14: "new pending"}
    result = active_entity_map(
        configured, discovered, {13}, blocked={12}
    )
    assert result == {11: "configured group", 13: "new approved"}
    assert active_entity_map(configured, discovered, {13}, blocked={12},
                             selected_only=True) == {13: "new approved"}
    assert active_entity_map(configured, discovered, {11, 13}, blocked={12},
                             selected_only=True) == {
        11: "configured group", 13: "new approved"
    }


def test_legacy_configured_sources_preserved_by_default():
    assert active_entity_map({1: "legacy"}, {2: "found"}, {2}) == {
        1: "legacy", 2: "found"
    }


def test_dashboard_control_requires_dedicated_write_credentials(monkeypatch):
    from telegram_monitor.cloud import api

    monkeypatch.setenv("CLOUD_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("CLOUD_DISCOVERY_UI_WRITE", "true")
    monkeypatch.delenv("CLOUD_SOURCE_CONTROL_DATABASE_URL", raising=False)
    assert api._source_writes_enabled() is False

    monkeypatch.setenv("CLOUD_SOURCE_CONTROL_DATABASE_URL", "sqlite://")
    assert api._source_writes_enabled() is True

    monkeypatch.setenv("CLOUD_DISCOVERY_UI_WRITE", "false")
    assert api._source_writes_enabled() is False


def test_source_controller_rejects_missing_role_or_invalid_origin(monkeypatch):
    from fastapi.testclient import TestClient
    from telegram_monitor.cloud import api

    monkeypatch.setenv("DASHBOARD_USER", "operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "temporary-test-password-1234")
    monkeypatch.setenv("CLOUD_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("CLOUD_DISCOVERY_UI_WRITE", "true")
    monkeypatch.delenv("CLOUD_SOURCE_CONTROL_DATABASE_URL", raising=False)

    client = TestClient(api.app)
    auth = ("operator", "temporary-test-password-1234")
    response = client.post("/api/sources/11", auth=auth,
                           json={"action": "disable"},
                           headers={"origin": "http://testserver",
                                    "x-tm-action": "1"})
    assert response.status_code == 403

    monkeypatch.setenv("CLOUD_SOURCE_CONTROL_DATABASE_URL", "sqlite://")
    response = client.post("/api/sources/11", auth=auth,
                           json={"action": "disable"},
                           headers={"origin": "https://evil.example",
                                    "x-tm-action": "1"})
    assert response.status_code == 403


def test_source_controller_allows_authenticated_manual_selection(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from telegram_monitor.cloud import api

    registry = SourceRegistry("sqlite:///" + str(tmp_path / "selected.db"), initialize=True)
    registry.record_scan([_source(501)], auto_public=False)

    monkeypatch.setenv("DASHBOARD_USER", "operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "temporary-test-password-1234")
    monkeypatch.setenv("CLOUD_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("CLOUD_DISCOVERY_UI_WRITE", "true")
    monkeypatch.setenv("CLOUD_SOURCE_CONTROL_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(api, "source_decision_registry", lambda: registry)
    try:
        client = TestClient(api.app)
        response = client.post(
            "/api/sources/501",
            auth=("operator", "temporary-test-password-1234"),
            json={"action": "approve"},
            headers={"origin": "http://testserver", "x-tm-action": "1"},
        )
        assert response.status_code == 200
        assert response.json() == {"peer_id": 501, "status": "approved"}
        assert registry.selection_ids() == ({501}, set())
    finally:
        registry.dispose()
