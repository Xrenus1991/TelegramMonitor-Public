"""Validate the reference Compose security boundaries; no Docker operations."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
reference = ROOT / "deploy" / "compose.reference.yml"


def main():
    cfg = yaml.safe_load(reference.read_text(encoding="utf-8"))
    services = cfg["services"]
    dashboard = services["dashboard"]
    sherlock = services["sherlock_osint"]
    assert sherlock.get("profiles") == ["sherlock"], "Sherlock must be manually enabled"
    assert sherlock["read_only"] is True
    assert not sherlock.get("ports"), "Sherlock must not be published externally"
    assert sherlock["networks"] == ["sherlock_net"], "Sherlock cannot reach PostgreSQL network"
    assert "default" in dashboard["networks"]
    assert dashboard["ports"] == ["127.0.0.1:8080:8080"]
    assert "OPENAI_API_KEY" not in sherlock.get("environment", {})
    assert "TG_API_HASH" not in sherlock.get("environment", {})
    assert services["collector"]["environment"]["CLOUD_COMMENTS_ENABLED"] == "${CLOUD_COMMENTS_ENABLED:-false}"
    assert dashboard["environment"]["SHERLOCK_OSINT_ENABLED"] == "${SHERLOCK_OSINT_ENABLED:-false}"
    print("REFERENCE_COMPOSE_BOUNDARIES_OK")


if __name__ == "__main__":
    main()
