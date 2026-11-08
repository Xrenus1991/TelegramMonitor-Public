"""Conservative pre-publish check. Not a replacement for dedicated secret scanners.

Prints only filenames and finding types, never discovered values.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

PATTERNS = {
    "private-key-block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "openai-api-key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
    "github-token": re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}"),
    "telegram-bot-token": re.compile(r"(?<!\w)\d{6,12}:[A-Za-z0-9_-]{30,}(?!\w)"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yml", ".yaml", ".html", ".json", ".sh", ".gitignore"}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
DANGEROUS_SUFFIXES = {".session", ".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}


def check(root: Path) -> list[tuple[str, str]]:
    """Return relative locations and type labels, without leaking secret contents."""
    root = root.resolve()
    findings: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        name = path.name.lower()
        if name == ".env" or name.startswith(".env.") and not name.endswith(".example"):
            findings.append((relative, "env-file"))
        if path.suffix.lower() in DANGEROUS_SUFFIXES or name.endswith(".session-journal"):
            findings.append((relative, "credential-or-db-file"))
        if path.parts[-2:-1] == ("configs",) and name.endswith(".json") and not name.endswith(".example.json"):
            findings.append((relative, "runtime-config"))
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > 2_000_000:
            findings.append((relative, "oversized-text-file"))
            continue
        data = path.read_text(encoding="utf-8", errors="replace")
        for kind, pattern in PATTERNS.items():
            if pattern.search(data):
                findings.append((relative, kind))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", type=Path)
    args = parser.parse_args()
    problems = check(args.root)
    for path, kind in problems:
        print(f"REVIEW_REQUIRED: {path} [{kind}]")
    if problems:
        return 1
    print("SOURCE_PRECHECK_OK (limited patterns; not a full secrets audit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
