"""Check embedded dashboard JavaScript syntax without executing it or using the network."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HTMLS = [ROOT / "telegram_monitor" / "cloud" / name for name in
         ("dashboard_v8_3.html", "sherlock_full.html")]


def main() -> int:
    for html in HTMLS:
        markup = html.read_text(encoding="utf-8")
        scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script\s*>", markup, re.I | re.S)
        if len(scripts) != 1:
            print(f"Expected exactly one script in {html}", file=sys.stderr)
            return 1
        if "innerHTML" in scripts[0] or "document.write(" in scripts[0]:
            print(f"Forbidden HTML injection sink detected in {html}", file=sys.stderr)
            return 1
        with tempfile.TemporaryDirectory() as dirname:
            name = Path(dirname) / "dashboard.js"
            name.write_text(scripts[0], encoding="utf-8")
            check = subprocess.run(["node", "--check", str(name)], check=False, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if check.returncode:
                print(check.stderr, file=sys.stderr)
                return check.returncode
    print("DASHBOARD_JS_SYNTAX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
