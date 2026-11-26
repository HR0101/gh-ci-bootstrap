#!/usr/bin/env python3
"""Generate .github/dependabot.yml tailored to the target repository's ecosystems."""
from __future__ import annotations

import os
import sys


def detect_ecosystems(repo_root: str) -> list[tuple[str, str]]:
    ecosystems: list[tuple[str, str]] = [("github-actions", "/")]

    if os.path.isfile(os.path.join(repo_root, "package.json")):
        # bun.lock（テキスト形式）のみ公式 Dependabot "bun" エコシステム対象．bun.lockb 等は npm 扱い
        if os.path.isfile(os.path.join(repo_root, "bun.lock")):
            ecosystems.append(("bun", "/"))
        else:
            ecosystems.append(("npm", "/"))

    if os.path.isfile(os.path.join(repo_root, "uv.lock")):
        ecosystems.append(("uv", "/"))
    elif any(
        os.path.isfile(os.path.join(repo_root, f))
        for f in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py")
    ):
        ecosystems.append(("pip", "/"))

    if os.path.isfile(os.path.join(repo_root, "Package.swift")):
        ecosystems.append(("swift", "/"))

    if os.path.isfile(os.path.join(repo_root, "pubspec.yaml")):
        ecosystems.append(("pub", "/"))

    if not os.path.isdir(os.path.join(repo_root, "ProjectSettings")):
        if any(
            e.endswith((".sln", ".slnx", ".csproj", ".fsproj"))
            and os.path.isfile(os.path.join(repo_root, e))
            for e in os.listdir(repo_root)
        ):
            ecosystems.append(("nuget", "/"))

    if any(
        (e == "Dockerfile" or e.startswith("Dockerfile.") or e.endswith(".Dockerfile"))
        and os.path.isfile(os.path.join(repo_root, e))
        for e in os.listdir(repo_root)
    ):
        ecosystems.append(("docker", "/"))

    if os.path.isfile(os.path.join(repo_root, "Gemfile")):
        ecosystems.append(("bundler", "/"))

    return ecosystems


def render_dependabot_yaml(ecosystems: list[tuple[str, str]]) -> str:
    lines = [
        "version: 2",
        "updates:",
    ]
    for eco, directory in ecosystems:
        lines.extend([
            f'  - package-ecosystem: "{eco}"',
            f'    directory: "{directory}"',
            "    schedule:",
            '      interval: "weekly"',
            "    open-pull-requests-limit: 5",
            "    groups:",
            "      minor-and-patch:",
            "        update-types:",
            '          - "minor"',
            '          - "patch"',
        ])
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: generate_dependabot.py <repo_root>", file=sys.stderr)
        sys.exit(2)
    repo_root = sys.argv[1]
    ecosystems = detect_ecosystems(repo_root)
    sys.stdout.write(render_dependabot_yaml(ecosystems))


if __name__ == "__main__":
    main()
