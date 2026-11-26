#!/usr/bin/env python3
"""Sync scripts/repo_check.py into .github/workflows/common-ci.yml (single source of truth)."""
from pathlib import Path
import re
import textwrap

ROOT = Path(__file__).resolve().parent.parent
CHECK_PY = ROOT / "scripts" / "repo_check.py"
COMMON_CI = ROOT / ".github" / "workflows" / "common-ci.yml"

MARKER_START = "          python3 - --tracked --ci << 'REPO_CHECK_PYEOF'\n"
MARKER_END = "          REPO_CHECK_PYEOF\n"


def render_common_ci() -> str:
  py_src = CHECK_PY.read_text(encoding="utf-8")
  indented = textwrap.indent(py_src, "          ")
  if not indented.endswith("\n"):
    indented += "\n"
  wf_src = COMMON_CI.read_text(encoding="utf-8")
  pattern = re.compile(re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END), flags=re.S)
  if not pattern.search(wf_src):
    raise RuntimeError("Placeholder block not found in common-ci.yml")
  replacement = MARKER_START + indented + MARKER_END
  return pattern.sub(lambda _: replacement, wf_src, count=1)


def main() -> None:
  updated = render_common_ci()
  COMMON_CI.write_text(updated, encoding="utf-8")
  print("Synced scripts/repo_check.py -> .github/workflows/common-ci.yml")


if __name__ == "__main__":
  main()
