#!/usr/bin/env python3
"""Universal repository quality, secret, hygiene, and syntax checker.

Used identically by:
  - Local CLI (`ci-check`)
  - Global git pre-commit hook (`pre-commit-ci-check --staged`)
  - GitHub Actions Reusable Workflow (`.github/workflows/common-ci.yml --ci`)
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import tomllib
except ImportError:  # Python < 3.11 fallback
    tomllib = None

try:
    import yaml
except ImportError:
    yaml = None

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_GIT_ERROR = 2
EXIT_INTERNAL = 3

IGNORE_MARKER = "ci-" + "ignore"

EXCLUDED_LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
    "Pipfile.lock",
    "bun.lock",
    "Package.resolved",
    "Podfile.lock",
    "pubspec.lock",
    "Gemfile.lock",
}

ALLOWED_SECRET_BASENAMES = {
    "GoogleService-Info.plist",
    "google-services.json",
    "firebase_options.dart",
}

JSONC_BASENAMES = {
    "biome.json",
    ".eslintrc.json",
    ".devcontainer.json",
    "turbo.json",
    "wrangler.json",
    "tslint.json",
}
JSONC_DIR_PREFIXES = (".vscode/", ".devcontainer/")

RUBY_SPECIAL_BASENAMES = {"Podfile", "Gemfile", "Fastfile", "Appfile", "Matchfile", "Rakefile", "Brewfile"}

# 常に絵文字として表示されるコードポイント（Emoji_Presentation=Yes + 補助絵文字ブロック）
ALWAYS_EMOJI_RANGES = [
    (0x1F000, 0x1FAFF),
    (0x231A, 0x231B),
    (0x23E9, 0x23EC),
    (0x23F0, 0x23F0),
    (0x23F3, 0x23F3),
    (0x25FD, 0x25FE),
    (0x2614, 0x2615),
    (0x2648, 0x2653),
    (0x267F, 0x267F),
    (0x2693, 0x2693),
    (0x26A1, 0x26A1),
    (0x26AA, 0x26AB),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26CE, 0x26CE),
    (0x26D4, 0x26D4),
    (0x26EA, 0x26EA),
    (0x26F2, 0x26F3),
    (0x26F5, 0x26F5),
    (0x26FA, 0x26FA),
    (0x26FD, 0x26FD),
    (0x2705, 0x2705),
    (0x270A, 0x270B),
    (0x2728, 0x2728),
    (0x274C, 0x274C),
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),
    (0x2795, 0x2797),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
    (0xFE0F, 0xFE0F),  # 絵文字表示セレクタ（☎/✉/✌/⚙/▶/➡/〰 等の絵文字表記は全てここで捕捉）
]

# 日本語文書でも裸文字として使われ得る記号（標準モードでは直後に U+FE0F がある場合のみ絵文字扱い，
# --strict-emoji / CI_STRICT_EMOJI=true 指定時は単体でも検出）
TEXT_SYMBOL_EMOJI_RANGES = [
    (0x23ED, 0x23EF),
    (0x23F8, 0x23FA),
    (0x25B6, 0x25B6),
    (0x25C0, 0x25C0),
    (0x2600, 0x2604),
    (0x260E, 0x260E),
    (0x2611, 0x2611),
    (0x2618, 0x2618),
    (0x261D, 0x261D),
    (0x2620, 0x2620),
    (0x2622, 0x2623),
    (0x2626, 0x2626),
    (0x262A, 0x262A),
    (0x262E, 0x262F),
    (0x2638, 0x263A),
    (0x267B, 0x267B),
    (0x2692, 0x2692),
    (0x2694, 0x2697),
    (0x2699, 0x2699),
    (0x269B, 0x269C),
    (0x26A0, 0x26A0),
    (0x26B0, 0x26B1),
    (0x26C8, 0x26C8),
    (0x26CF, 0x26CF),
    (0x26D1, 0x26D1),
    (0x26D3, 0x26D3),
    (0x26E9, 0x26E9),
    (0x26F0, 0x26F1),
    (0x26F4, 0x26F4),
    (0x26F7, 0x26F9),
    (0x2702, 0x2702),
    (0x2708, 0x2709),
    (0x270C, 0x270D),
    (0x270F, 0x270F),
    (0x2712, 0x2712),
    (0x2714, 0x2714),
    (0x2716, 0x2716),
    (0x2744, 0x2744),
    (0x2747, 0x2747),
    (0x2763, 0x2764),
    (0x27A1, 0x27A1),
    (0x2934, 0x2935),
    (0x2B05, 0x2B07),
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3297),
    (0x3299, 0x3299),
]

ALWAYS_EMOJI_PATTERN = re.compile(
    "[" + "".join(f"{chr(s)}-{chr(e)}" for s, e in ALWAYS_EMOJI_RANGES) + "]"
)
STRICT_EMOJI_PATTERN = re.compile(
    "[" + "".join(f"{chr(s)}-{chr(e)}" for s, e in (ALWAYS_EMOJI_RANGES + TEXT_SYMBOL_EMOJI_RANGES)) + "]"
)

SENSITIVE_EXACT_NAMES = {
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".netrc",
    ".htpasswd",
    ".envrc",
    "credentials.json",
    "terraform.tfstate",
}
SENSITIVE_EXTENSIONS = {".p8", ".p12", ".pfx", ".jks", ".keystore", ".tfvars"}
ALLOWED_ENV_SUFFIXES = {"example", "sample", "template", "schema", "dist", "defaults", "test"}

SECRET_PATTERNS = [
    ("Private Key", re.compile("-" * 5 + r"BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY" + "-" * 5)),
    ("AWS Access Key", re.compile(r"\b(?:AK" + r"IA|AS" + r"IA)[0-9A-Z]{16}\b")),
    ("GitHub Token", re.compile(r"\b(?:gh" + r"p|gh" + r"o|gh" + r"u|gh" + r"s|gh" + r"r)_[A-Za-z0-9]{36}\b")),
    ("GitHub Fine-Grained PAT", re.compile(r"\bgithub_" + r"pat_[A-Za-z0-9_]{22,}\b")),
    ("GitLab PAT", re.compile(r"\bgl" + r"pat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("Anthropic API Key", re.compile(r"\bsk-" + r"ant-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("OpenAI API Key", re.compile(r"\bsk-(?:pr" + r"oj|svc" + r"acct|ad" + r"min)-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("Google API Key", re.compile(r"\bAI" + r"za[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])")),
    ("Google OAuth Secret", re.compile(r"\bGOC" + r"SPX-[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")),
    ("Slack Token", re.compile(r"\bxo" + r"x[baprs]-[0-9A-Za-z-]{10,}(?![A-Za-z0-9-])")),
    ("Slack Webhook URL", re.compile(r"https://hooks\.sl" + r"ack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+")),
    ("Discord Webhook URL", re.compile(r"https://(?:ptb\.|canary\.)?dis" + r"cord(?:app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+")),
    ("Stripe Live Key", re.compile(r"\b[rs]k_" + r"live_[0-9A-Za-z]{20,}\b")),
    ("SendGrid API Key", re.compile(r"\bS" + r"G\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")),
    ("npm Token", re.compile(r"\bnp" + r"m_[A-Za-z0-9]{36}\b")),
    ("Hugging Face Token", re.compile(r"\bh" + r"f_[A-Za-z0-9]{30,}\b")),
]

PLACEHOLDER_HINTS = (
    "example",
    "sample",
    "dummy",
    "placeholder",
    "changeme",
    "your_",
    "your-",
    "xxxx",
    "00000000",
    "12345678",
)

DOCKER_INSTRUCTIONS = {
    "FROM", "RUN", "CMD", "LABEL", "MAINTAINER", "EXPOSE", "ENV",
    "ADD", "COPY", "ENTRYPOINT", "VOLUME", "USER", "WORKDIR", "ARG",
    "ONBUILD", "STOPSIGNAL", "HEALTHCHECK", "SHELL",
}

CONFLICT_START = "<" * 7 + " "
CONFLICT_END = ">" * 7 + " "
BINARY_CHECK_BYTES = 8000


def parse_bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def parse_csv_env(name: str) -> list[str]:
    val = os.environ.get(name, "").strip()
    if not val:
        return []
    return [item.strip() for item in val.split(",") if item.strip()]


def parse_float_env(name: str, default: float) -> float:
    val = os.environ.get(name, "").strip()
    if not val:
        return default
    num = float(val)
    if num <= 0:
        raise ValueError(f"{name} must be > 0, got {val!r}")
    return num


def escape_prop(value: object) -> str:
    return (
        str(value)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def escape_data(value: object) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_annotation(
    level: str,
    message: str,
    error_details: list[str],
    file_path: str | None = None,
    line: int | None = None,
    col: int | None = None,
    ci_mode: bool = False,
) -> None:
    if ci_mode:
        props = []
        if file_path is not None:
            props.append(f"file={escape_prop(file_path)}")
        if line is not None:
            props.append(f"line={line}")
        if col is not None:
            props.append(f"col={col}")
        prop_str = (" " + ",".join(props)) if props else ""
        print(f"::{level}{prop_str}::{escape_data(message)}")
    else:
        loc = file_path or "(repo)"
        if line is not None:
            loc = f"{loc}:{line}"
            if col is not None:
                loc = f"{loc}:{col}"
        tag = "ERROR" if level == "error" else "WARN"
        print(f"[{tag}] {loc}: {message}")

    if level == "error" and len(error_details) < 50:
        loc = file_path or "(repo)"
        if line is not None:
            loc = f"{loc}:{line}"
        error_details.append(f"- `{loc}`: {message}")


def print_doctor() -> int:
    tools = {
        "python3": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "tomllib": "built-in" if tomllib is not None else "MISSING (Python 3.11+ required for TOML checks)",
        "PyYAML": "installed" if yaml is not None else "not installed (falls back to ruby psych)",
        "ruby": shutil.which("ruby") or "MISSING (needed for Ruby syntax & YAML fallback)",
        "node": shutil.which("node") or "MISSING (needed for plain JS syntax check)",
        "shellcheck": shutil.which("shellcheck") or "MISSING (recommended to match CI shellcheck)",
        "bash": shutil.which("bash") or "MISSING",
        "sh": shutil.which("sh") or "MISSING",
        "zsh": shutil.which("zsh") or "not installed",
    }
    print("ci-check environment diagnostics:")
    for k, v in tools.items():
        print(f"  {k:12s}: {v}")
    return EXIT_OK


def list_target_files(mode: str, error_details: list[str], ci_mode: bool) -> list[str]:
    if mode == "staged":
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
    elif mode == "tracked":
        cmd = ["git", "ls-files", "-z"]
    else:
        cmd = ["git", "ls-files", "-co", "--exclude-standard", "-z"]
    try:
        res = subprocess.run(cmd, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as err:
        emit_annotation("error", f"対象ファイル一覧の取得に失敗しました: {err}", error_details, ci_mode=ci_mode)
        sys.exit(EXIT_GIT_ERROR)
    return [p for p in os.fsdecode(res.stdout).split("\0") if p]


def get_lfs_paths(files: list[str]) -> set[str]:
    """Git LFS 管理対象ファイルを 1 プロセスで一括判定する．"""
    if not files:
        return set()
    payload = b"".join(os.fsencode(p) + b"\0" for p in files)
    res = subprocess.run(
        ["git", "check-attr", "filter", "-z", "--stdin"],
        input=payload,
        capture_output=True,
    )
    if res.returncode != 0:
        return set()
    parts = res.stdout.split(b"\0")
    lfs_set: set[str] = set()
    # 出力は <path>\0<attr>\0<value>\0 の 3 要素組
    i = 0
    while i + 2 < len(parts):
        fpath = os.fsdecode(parts[i])
        val = os.fsdecode(parts[i + 2])
        if val == "lfs":
            lfs_set.add(fpath)
        i += 3
    return lfs_set


def read_staged_blobs(files: list[str]) -> dict[str, tuple[int, bytes | None]]:
    """git cat-file --batch 1 プロセスでステージ済みファイルの内容を一括取得する．"""
    result: dict[str, tuple[int, bytes | None]] = {}
    if not files:
        return result
    query = "".join(f":0:{p}\n" for p in files).encode("utf-8")
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_data, _ = proc.communicate(query)
    if proc.returncode != 0:
        raise RuntimeError("git cat-file --batch failed")

    pos = 0
    total = len(stdout_data)
    for path in files:
        nl = stdout_data.find(b"\n", pos)
        if nl < 0:
            break
        header = stdout_data[pos:nl].decode("utf-8", errors="replace")
        pos = nl + 1
        tokens = header.split()
        if len(tokens) >= 3 and tokens[1] == "blob":
            size = int(tokens[2])
            blob = stdout_data[pos : pos + size]
            pos += size + 1  # trailing LF
            result[path] = (size, blob)
        else:
            result[path] = (0, None)
    assert pos <= total
    return result


def get_working_size_and_head(path: str) -> tuple[int, bytes]:
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    head = b""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        pass
    return size, head


def read_working_bytes(path: str, error_details: list[str], ci_mode: bool) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as err:
        emit_annotation("warning", f"読み込めませんでした: {err}", error_details, file_path=path, ci_mode=ci_mode)
        return None


def is_binary_bytes(data: bytes) -> bool:
    return b"\0" in data[:BINARY_CHECK_BYTES]


def is_sensitive_file_name(path: str, head_bytes: bytes) -> bool:
    base = os.path.basename(path)
    lower = base.lower()
    if lower in SENSITIVE_EXACT_NAMES or lower.startswith("terraform.tfstate."):
        return True
    if lower.startswith("service-account") and lower.endswith(".json"):
        return True
    if lower.startswith("authkey_") and lower.endswith(".p8"):
        return True
    _, ext = os.path.splitext(lower)
    if ext in SENSITIVE_EXTENSIONS:
        return True
    if ext == ".key":
        if head_bytes.startswith(b"PK\x03\x04"):
            return False
        return True
    if lower == ".env" or lower.startswith(".env."):
        if lower == ".env":
            return True
        segments = set(lower.split(".")[2:])
        if not (segments & ALLOWED_ENV_SUFFIXES):
            return True
    return False


def is_placeholder_match(matched: str) -> bool:
    lower = matched.lower()
    return any(hint in lower for hint in PLACEHOLDER_HINTS)


def is_jsonc_file(path: str) -> bool:
    base = os.path.basename(path)
    if base in JSONC_BASENAMES:
        return True
    if (base.startswith("tsconfig") or base.startswith("jsconfig")) and base.endswith(".json"):
        return True
    return path.startswith(JSONC_DIR_PREFIXES)


def is_dockerfile(base: str) -> bool:
    return base == "Dockerfile" or base.startswith("Dockerfile.") or base.endswith(".Dockerfile")


def is_ruby_file(base: str, ext: str) -> bool:
    return ext in (".rb", ".podspec") or base in RUBY_SPECIAL_BASENAMES


def detect_shell_kind(ext: str, first_line: str) -> str | None:
    clean_first = first_line.lstrip("\ufeff")
    if ext == ".zsh":
        return "zsh"
    if ext == ".bash":
        return "bash"
    if ext == ".sh":
        if "zsh" in clean_first:
            return "zsh"
        if "/bin/sh" in clean_first or "env sh" in clean_first:
            return "sh"
        return "bash"
    if not ext and clean_first.startswith("#!"):
        if re.search(r"(?:/|\benv\s+(?:-\S+\s+)*)zsh\b", clean_first):
            return "zsh"
        if re.search(r"(?:/|\benv\s+(?:-\S+\s+)*)bash\b", clean_first):
            return "bash"
        if re.search(r"(?:/|\benv\s+(?:-\S+\s+)*)sh\b", clean_first):
            return "sh"
    return None


def validate_yaml(path: str, text: str, tmp_file_path: str | None, has_ruby: bool, skipped_tools: set[str]) -> str | None:
    if "{{" in text and "}}" in text and not path.startswith(".github/"):
        return None
    if yaml is not None:
        try:
            loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
            list(yaml.compose_all(text, Loader=loader))
        except yaml.YAMLError as err:
            return str(err).splitlines()[0]
        return None
    if has_ruby:
        target = tmp_file_path or path
        res = subprocess.run(
            ["ruby", "-rpsych", "-e", "Psych.parse_stream(File.read(ARGV[0]))", "--", target],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            msg = (res.stderr or res.stdout).strip().splitlines()
            return msg[0] if msg else "YAML syntax error"
        return None
    skipped_tools.add("YAML (PyYAML/ruby)")
    return None


def validate_shell(
    target_path: str,
    shell_kind: str,
    has_shellcheck: bool,
    skipped_tools: set[str],
) -> list[tuple[int | None, int | None, str]]:
    errors: list[tuple[int | None, int | None, str]] = []
    if shutil.which(shell_kind) is not None:
        res = subprocess.run([shell_kind, "-n", "--", target_path], capture_output=True, text=True)
        if res.returncode != 0:
            msg = (res.stderr or res.stdout).strip().splitlines()
            errors.append((None, None, msg[0] if msg else f"{shell_kind} -n failed"))
            return errors
    else:
        skipped_tools.add(f"Shell ({shell_kind})")

    if shell_kind in ("sh", "bash"):
        if has_shellcheck:
            res = subprocess.run(
                ["shellcheck", "-s", shell_kind, "-S", "error", "-f", "gcc", "--", target_path],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                for line in res.stdout.splitlines():
                    m = re.match(r"^.*?:(\d+):(\d+):\s*(?:error|warning|note):\s*(.+)$", line)
                    if m:
                        errors.append((int(m.group(1)), int(m.group(2)), m.group(3)))
                    elif line.strip():
                        errors.append((None, None, line.strip()))
        else:
            skipped_tools.add("shellcheck")
    return errors


def validate_ruby(target_path: str, has_ruby: bool, skipped_tools: set[str]) -> str | None:
    if not has_ruby:
        skipped_tools.add("ruby")
        return None
    res = subprocess.run(["ruby", "-c", "--", target_path], capture_output=True, text=True)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip().splitlines()
        return msg[0] if msg else "Ruby syntax error"
    return None


def validate_plain_js(orig_path: str, target_path: str, has_package_json: bool, has_node: bool, skipped_tools: set[str]) -> str | None:
    if has_package_json or orig_path.endswith(".min.js"):
        return None
    if not has_node:
        skipped_tools.add("node")
        return None
    res = subprocess.run(["node", "--check", "--", target_path], capture_output=True, text=True)
    if res.returncode != 0:
        combined = (res.stderr or res.stdout).strip()
        if "Cannot use import statement outside a module" in combined:
            return None
        lines = [ln for ln in combined.splitlines() if ln.strip()]
        return lines[-1] if lines else "JavaScript syntax error"
    return None


def validate_dockerfile(text: str) -> list[tuple[int, str]]:
    clean_text = text.lstrip("\ufeff")
    errors: list[tuple[int, str]] = []
    has_from = False
    continuation = False
    heredoc_queue: list[str] = []
    escape_char = "\\"
    saw_non_comment = False

    for line_num, raw_line in enumerate(clean_text.splitlines(), start=1):
        if heredoc_queue:
            if raw_line.strip() == heredoc_queue[0]:
                heredoc_queue.pop(0)
            continue

        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if not saw_non_comment:
                em = re.match(r"^#\s*escape\s*=\s*([\\`])\s*$", stripped, re.I)
                if em:
                    escape_char = em.group(1)
            continue

        saw_non_comment = True
        if not continuation:
            token = stripped.split(None, 1)[0].upper()
            if token not in DOCKER_INSTRUCTIONS:
                errors.append((line_num, f"未知の Dockerfile 命令です: {token}"))
            elif token == "FROM":
                has_from = True

        markers = re.findall(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", stripped)
        if markers:
            heredoc_queue.extend(markers)
            continuation = False
        else:
            continuation = stripped.endswith(escape_char)

    if not has_from:
        errors.append((1, "Dockerfile に FROM 命令が見つかりません"))
    return errors


def write_summary(
    counts: dict[str, int],
    error_details: list[str],
    enable_no_emoji: bool,
    enable_secret_scan: bool,
    enable_hygiene: bool,
    enable_syntax: bool,
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    labels = [
        ("no_emoji", "絵文字禁止 (lint-no-emoji)", enable_no_emoji),
        ("secrets", "シークレット・機密ファイル検出", enable_secret_scan),
        ("hygiene", "リポジトリ衛生（コンフリクト / 巨大ファイル）", enable_hygiene),
        ("syntax", "構文チェック (JSON/TOML/YAML/Plist/Shell/Ruby/JS/Dockerfile)", enable_syntax),
    ]
    lines = [
        "### 共通チェック結果",
        "",
        "| チェック項目 | 状態 | 検出件数 |",
        "| --- | --- | ---: |",
    ]
    for key, title, enabled in labels:
        if not enabled:
            status = "スキップ"
            count_str = "-"
        elif counts[key] == 0:
            status = "OK"
            count_str = "0"
        else:
            status = "NG"
            count_str = str(counts[key])
        lines.append(f"| {title} | {status} | {count_str} |")
    lines.append("")
    if error_details:
        lines.append("<details><summary>検出した問題の詳細（最大 50 件）</summary>")
        lines.append("")
        lines.extend(error_details)
        lines.append("")
        lines.append("</details>")
        lines.append("")
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_with_optional_tempfile(data: bytes, staged: bool, ext: str, fn):
    if not staged:
        return fn(None)
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
        tf.write(data)
        tmp_path = tf.name
    try:
        return fn(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def run_checker(args: argparse.Namespace) -> int:
    enable_no_emoji = parse_bool_env("CI_ENABLE_NO_EMOJI", True)
    enable_secret_scan = parse_bool_env("CI_ENABLE_SECRET_SCAN", True)
    enable_hygiene = parse_bool_env("CI_ENABLE_HYGIENE", True)
    enable_syntax = parse_bool_env("CI_ENABLE_SYNTAX", True)
    strict_emoji = args.strict_emoji or parse_bool_env("CI_STRICT_EMOJI", False)
    max_file_size_mb = parse_float_env("CI_MAX_FILE_SIZE_MB", 10.0)
    allowed_emoji_prefixes = parse_csv_env("CI_ALLOWED_EMOJI_PREFIXES")
    allowed_secret_prefixes = parse_csv_env("CI_ALLOWED_SECRET_PREFIXES")

    emoji_pattern = STRICT_EMOJI_PATTERN if strict_emoji else ALWAYS_EMOJI_PATTERN

    mode = "staged" if args.staged else ("tracked" if args.tracked else "working")
    ci_mode = args.ci or (os.environ.get("GITHUB_ACTIONS") == "true")

    has_ruby = shutil.which("ruby") is not None
    has_node = shutil.which("node") is not None
    has_shellcheck = shutil.which("shellcheck") is not None
    has_package_json = os.path.isfile("package.json")

    counts = {"no_emoji": 0, "secrets": 0, "hygiene": 0, "syntax": 0}
    error_details: list[str] = []
    skipped_tools: set[str] = set()
    max_bytes = int(max_file_size_mb * 1024 * 1024)

    files = list_target_files(mode, error_details, ci_mode)
    if not args.staged:
        files = [p for p in files if os.path.isfile(p) and not os.path.islink(p)]

    lfs_paths = get_lfs_paths(files)
    staged_blobs = read_staged_blobs(files) if args.staged else {}

    for path in files:
        if path in lfs_paths:
            continue

        base = os.path.basename(path)
        _, ext = os.path.splitext(base.lower())
        is_lockfile = base in EXCLUDED_LOCKFILES
        is_secret_allowed = (
            base in ALLOWED_SECRET_BASENAMES
            or any(path.startswith(p) for p in allowed_secret_prefixes)
        )

        if args.staged:
            size, data = staged_blobs.get(path, (0, None))
            head_bytes = data[:16] if data is not None else b""
        else:
            size, head_bytes = get_working_size_and_head(path)
            data = None

        if enable_hygiene and size > max_bytes:
            if enable_secret_scan and not is_secret_allowed and is_sensitive_file_name(path, head_bytes):
                emit_annotation(
                    "error",
                    "機密ファイルの可能性があるファイルが追跡されています（.gitignore への追加を検討してください）",
                    error_details,
                    file_path=path,
                    ci_mode=ci_mode,
                )
                counts["secrets"] += 1
            size_mb = size / (1024 * 1024)
            emit_annotation(
                "error",
                f"ファイルサイズが上限（{max_file_size_mb:g}MB）を超えています（{size_mb:.1f}MB）",
                error_details,
                file_path=path,
                ci_mode=ci_mode,
            )
            counts["hygiene"] += 1
            continue

        if not args.staged:
            data = read_working_bytes(path, error_details, ci_mode)
        if data is None:
            continue

        if enable_secret_scan and not is_secret_allowed:
            if is_sensitive_file_name(path, data[:16]):
                emit_annotation(
                    "error",
                    "機密ファイルの可能性があるファイルが追跡されています（.gitignore への追加を検討してください）",
                    error_details,
                    file_path=path,
                    ci_mode=ci_mode,
                )
                counts["secrets"] += 1

        if enable_syntax and ext in (".plist", ".entitlements", ".xcprivacy"):
            try:
                plistlib.loads(data)
            except Exception as err:
                emit_annotation("error", f"Plist 構文エラー: {err}", error_details, file_path=path, ci_mode=ci_mode)
                counts["syntax"] += 1

        if is_binary_bytes(data):
            continue

        strict_utf8 = True
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            strict_utf8 = False
            text = data.decode("utf-8", errors="replace")

        lines = text.split("\n")

        if enable_no_emoji and strict_utf8 and not is_lockfile and not any(path.startswith(p) for p in allowed_emoji_prefixes):
            for line_num, line in enumerate(lines, start=1):
                if IGNORE_MARKER in line:
                    continue
                for match in emoji_pattern.finditer(line):
                    cp = ord(match.group())
                    emit_annotation(
                        "error",
                        f"絵文字 U+{cp:04X} が含まれています",
                        error_details,
                        file_path=path,
                        line=line_num,
                        col=match.start() + 1,
                        ci_mode=ci_mode,
                    )
                    counts["no_emoji"] += 1

        if enable_secret_scan and not is_lockfile and not is_secret_allowed:
            for line_num, line in enumerate(lines, start=1):
                if IGNORE_MARKER in line:
                    continue
                for label, pattern in SECRET_PATTERNS:
                    for match in pattern.finditer(line):
                        if not is_placeholder_match(match.group(0)):
                            emit_annotation(
                                "error",
                                f"{label} の可能性があります（意図的なテストデータの場合は行に {IGNORE_MARKER} を付与してください）",
                                error_details,
                                file_path=path,
                                line=line_num,
                                col=match.start() + 1,
                                ci_mode=ci_mode,
                            )
                            counts["secrets"] += 1

        if enable_hygiene:
            for line_num, line in enumerate(lines, start=1):
                if IGNORE_MARKER in line:
                    continue
                if line.startswith(CONFLICT_START) or line.startswith(CONFLICT_END):
                    emit_annotation(
                        "error",
                        "マージコンフリクトのマーカーが残っています",
                        error_details,
                        file_path=path,
                        line=line_num,
                        ci_mode=ci_mode,
                    )
                    counts["hygiene"] += 1

        if enable_syntax and strict_utf8:
            first_line = lines[0] if lines else ""
            shell_kind = detect_shell_kind(ext, first_line)

            if ext in (".json", ".xcstrings") and not is_jsonc_file(path):
                try:
                    json.loads(data.decode("utf-8-sig"))
                except (json.JSONDecodeError, RecursionError) as err:
                    line_no = getattr(err, "lineno", None)
                    col_no = getattr(err, "colno", None)
                    msg = getattr(err, "msg", str(err))
                    emit_annotation(
                        "error",
                        f"JSON 構文エラー: {msg}",
                        error_details,
                        file_path=path,
                        line=line_no,
                        col=col_no,
                        ci_mode=ci_mode,
                    )
                    counts["syntax"] += 1
            elif ext == ".toml":
                if tomllib is None:
                    skipped_tools.add("TOML (tomllib requires Python 3.11+)")
                else:
                    try:
                        tomllib.loads(data.decode("utf-8-sig"))
                    except tomllib.TOMLDecodeError as err:
                        emit_annotation("error", f"TOML 構文エラー: {err}", error_details, file_path=path, ci_mode=ci_mode)
                        counts["syntax"] += 1
            elif ext in (".yml", ".yaml"):
                yaml_err = run_with_optional_tempfile(
                    data, args.staged, ext, lambda tp: validate_yaml(path, text, tp, has_ruby, skipped_tools)
                )
                if yaml_err:
                    emit_annotation("error", f"YAML 構文エラー: {yaml_err}", error_details, file_path=path, ci_mode=ci_mode)
                    counts["syntax"] += 1
            elif shell_kind is not None:
                sh_errors = run_with_optional_tempfile(
                    data,
                    args.staged,
                    ext or ".sh",
                    lambda tp: validate_shell(tp or path, shell_kind, has_shellcheck, skipped_tools),
                )
                for err_line, err_col, err_msg in sh_errors:
                    emit_annotation(
                        "error",
                        f"Shell チェックエラー: {err_msg}",
                        error_details,
                        file_path=path,
                        line=err_line,
                        col=err_col,
                        ci_mode=ci_mode,
                    )
                    counts["syntax"] += 1
            elif is_ruby_file(base, ext):
                rb_err = run_with_optional_tempfile(
                    data, args.staged, ".rb", lambda tp: validate_ruby(tp or path, has_ruby, skipped_tools)
                )
                if rb_err:
                    emit_annotation("error", f"Ruby 構文エラー: {rb_err}", error_details, file_path=path, ci_mode=ci_mode)
                    counts["syntax"] += 1
            elif ext in (".js", ".mjs", ".cjs"):
                js_err = run_with_optional_tempfile(
                    data,
                    args.staged,
                    ext,
                    lambda tp: validate_plain_js(path, tp or path, has_package_json, has_node, skipped_tools),
                )
                if js_err:
                    emit_annotation("error", f"JavaScript 構文エラー: {js_err}", error_details, file_path=path, ci_mode=ci_mode)
                    counts["syntax"] += 1
            elif is_dockerfile(base):
                for err_line, err_msg in validate_dockerfile(text):
                    emit_annotation(
                        "error",
                        f"Dockerfile チェックエラー: {err_msg}",
                        error_details,
                        file_path=path,
                        line=err_line,
                        ci_mode=ci_mode,
                    )
                    counts["syntax"] += 1

    if skipped_tools and not args.quiet:
        emit_annotation(
            "warning",
            f"ローカルに未導入のため一部検査をスキップしました: {', '.join(sorted(skipped_tools))}",
            error_details,
            ci_mode=ci_mode,
        )

    if ci_mode:
        write_summary(counts, error_details, enable_no_emoji, enable_secret_scan, enable_hygiene, enable_syntax)

    total_errors = sum(counts.values())
    if total_errors > 0:
        print(f"チェック失敗: 合計 {total_errors} 件の問題を検出しました（{counts}）")
        return EXIT_VIOLATION
    if not args.quiet:
        print(f"成功 ({len(files)} ファイル検査): 共通チェックで問題は検出されませんでした．")
    return EXIT_OK


def main() -> None:
    parser = argparse.ArgumentParser(description="Universal repository quality/security checker")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--staged", action="store_true", help="Inspect staged git index blobs only")
    scope.add_argument("--tracked", action="store_true", help="Inspect tracked git files only")
    parser.add_argument("--ci", action="store_true", help="Emit GitHub Actions ::error annotations")
    parser.add_argument("--strict-emoji", action="store_true", help="Also flag bare text symbols (e.g. U+260E, U+270C) without U+FE0F")
    parser.add_argument("--doctor", action="store_true", help="Print installed checker dependencies and exit")
    parser.add_argument("--quiet", action="store_true", help="Print errors only")
    args = parser.parse_args()

    if args.doctor:
        sys.exit(print_doctor())

    try:
        sys.exit(run_checker(args))
    except SystemExit:
        raise
    except Exception as err:
        print(f"[repo_check] Internal error ({type(err).__name__}): {err}", file=sys.stderr)
        sys.exit(EXIT_INTERNAL)


if __name__ == "__main__":
    main()
