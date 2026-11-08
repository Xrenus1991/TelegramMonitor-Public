"""Read-only deployment preflight; never opens credential files or contacts services.

Run on a staged release tree before attempting any production update.
A check of file presence is not a Docker/DB/Telegram functional test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


# Keys and sessions are NEVER inspected, only the presence of a generic .env.
CANDIDATE_COMPOSE = (
    "deploy/compose.yml", "deploy/compose.yaml", "deploy/docker-compose.yml",
    "compose.yml", "compose.yaml", "docker-compose.yml",
)


def assess(root: Path) -> dict:
    root = Path(root).expanduser().resolve()
    required_source = (
        "telegram_monitor/cloud/collector.py",
        "telegram_monitor/cloud/worker.py",
        "telegram_monitor/cloud/api.py",
        "telegram_monitor/cloud/db.py",
        "telegram_monitor/cloud/comments.py",
        "telegram_monitor/cloud/sherlock_proxy.py",
    )
    present = [name for name in required_source if (root / name).is_file()]
    absent = [name for name in required_source if name not in present]
    configs = [name for name in CANDIDATE_COMPOSE if (root / name).is_file()]
    # Do not print secrets; do not inspect values, permissions, keys or sessions.
    has_env = any((root / name).is_file() for name in ("deploy/.env", ".env"))
    backup_candidate = any((root / name).is_file() for name in
                           ("deploy/backup_loop.sh", "scripts/backup.sh", "deploy/backup.sh"))
    findings = []
    if absent:
        findings.append("Source modules missing; inspect the staged release tree")
    if not configs:
        findings.append("No live Compose file included; reference Compose is not a deployment")
    if not has_env:
        findings.append("No runtime .env file present in this tree; do not copy secrets into Git")
    if not backup_candidate:
        findings.append("Backup script not found at common paths; verify real backup procedure")
    return {
        "ready_for_manual_review": not absent and bool(configs) and has_env and backup_candidate,
        "deployment_tested": False,
        "source_files_present": len(present),
        "source_files_expected": len(required_source),
        "source_missing": absent,
        "compose_present": bool(configs),
        "env_file_present": has_env,
        "backup_script_present": backup_candidate,
        "findings": findings,
        "limitations": [
            "Read-only filename checks only; no secret contents inspected",
            "Does not validate Docker build, PostgreSQL restoration, Telegram or OpenAI",
            "Does not authorize deploying to the production server",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--strict", action="store_true", help="Exit 2 if manual-review prerequisites missing")
    opts = parser.parse_args(argv)
    result = assess(opts.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if opts.strict and not result["ready_for_manual_review"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
