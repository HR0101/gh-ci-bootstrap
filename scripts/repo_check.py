#!/usr/bin/env python3
"""Universal repository quality, secret, hygiene, and syntax checker.

Used identically by:
  - Local CLI (`ci-check`)
  - Global git pre-commit hook (`pre-commit-ci-check --staged`)
  - GitHub Actions Reusable Workflow (`.github/workflows/common-ci.yml --ci`)
"""
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
except ImportError:
  tomllib = None

try:
  import yaml
except ImportError:
  yaml = None


def parseBoolEnv(name, default):
  val = os.environ.get(name)
  if val is None or val == "":
    return default
  return val.strip().lower() in ("1", "true", "yes", "on")


def parseCsvEnv(name):
  val = os.environ.get(name, "").strip()
  if not val:
    return []
  return [item.strip() for item in val.split(",") if item.strip()]


# =====================================================================
# 設定（環境変数または workflow_call inputs から上書き可能）
# =====================================================================
ENABLE_NO_EMOJI = parseBoolEnv("CI_ENABLE_NO_EMOJI", True)
ENABLE_SECRET_SCAN = parseBoolEnv("CI_ENABLE_SECRET_SCAN", True)
ENABLE_HYGIENE = parseBoolEnv("CI_ENABLE_HYGIENE", True)
ENABLE_SYNTAX = parseBoolEnv("CI_ENABLE_SYNTAX", True)

# 巨大ファイルの上限（MB）
MAX_FILE_SIZE_MB = float(os.environ.get("CI_MAX_FILE_SIZE_MB") or 10)

# 行末等にこのマーカーを含む行は，絵文字・シークレット・コンフリクト検査をスキップ
IGNORE_MARKER = "ci-" + "ignore"

# ロックファイル（機械生成のため絵文字・シークレット検査から除外）
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

# Firebase / FlutterFire のクライアント公開設定（AIza... は公開前提の識別子のため既定で除外）
ALLOWED_SECRET_BASENAMES = {
  "GoogleService-Info.plist",
  "google-services.json",
  "firebase_options.dart",
}

ALLOWED_EMOJI_PATH_PREFIXES = parseCsvEnv("CI_ALLOWED_EMOJI_PREFIXES")
ALLOWED_SECRET_PATH_PREFIXES = parseCsvEnv("CI_ALLOWED_SECRET_PREFIXES")

# JSONC（コメントや末尾カンマを許容する JSON）として構文チェックを除外する対象
JSONC_BASENAMES = {
  "biome.json",
  ".eslintrc.json",
  ".devcontainer.json",
  "turbo.json",
  "wrangler.json",
  "tslint.json",
}
JSONC_DIR_PREFIXES = (".vscode/", ".devcontainer/")

# Ruby 構文チェック対象の特別ファイル名
RUBY_SPECIAL_BASENAMES = {"Podfile", "Gemfile", "Fastfile", "Appfile", "Matchfile", "Rakefile", "Brewfile"}

# 絵文字として扱うコードポイントの範囲
# 星(U+2605)・☆(U+2606)・音符(U+266A)・矢印(U+2192)・※(U+203B) 等を巻き込まないよう絞っている
EMOJI_RANGES = [
  (0x1F000, 0x1FAFF),
  (0x231A, 0x231B),
  (0x23E9, 0x23EF),
  (0x23F0, 0x23F0),
  (0x23F3, 0x23F3),
  (0x23F8, 0x23FA),
  (0x25B6, 0x25B6),
  (0x25C0, 0x25C0),
  (0x25FD, 0x25FE),
  (0x2600, 0x2604),
  (0x260E, 0x260E),
  (0x2611, 0x2611),
  (0x2614, 0x2615),
  (0x2618, 0x2618),
  (0x261D, 0x261D),
  (0x2620, 0x2620),
  (0x2622, 0x2623),
  (0x2626, 0x2626),
  (0x262A, 0x262A),
  (0x262E, 0x262F),
  (0x2638, 0x263A),
  (0x2648, 0x2653),
  (0x267B, 0x267B),
  (0x267F, 0x267F),
  (0x2692, 0x2697),
  (0x2699, 0x2699),
  (0x269B, 0x269C),
  (0x26A0, 0x26A1),
  (0x26AA, 0x26AB),
  (0x26B0, 0x26B1),
  (0x26BD, 0x26BE),
  (0x26C4, 0x26C5),
  (0x26C8, 0x26C8),
  (0x26CE, 0x26CE),
  (0x26CF, 0x26CF),
  (0x26D1, 0x26D1),
  (0x26D3, 0x26D4),
  (0x26E9, 0x26EA),
  (0x26F0, 0x26F5),
  (0x26F7, 0x26FA),
  (0x26FD, 0x26FD),
  (0x2702, 0x2702),
  (0x2705, 0x2705),
  (0x2708, 0x270D),
  (0x270F, 0x270F),
  (0x2712, 0x2712),
  (0x2714, 0x2714),
  (0x2716, 0x2716),
  (0x2728, 0x2728),
  (0x2744, 0x2744),
  (0x2747, 0x2747),
  (0x274C, 0x274C),
  (0x274E, 0x274E),
  (0x2753, 0x2755),
  (0x2757, 0x2757),
  (0x2763, 0x2764),
  (0x2795, 0x2797),
  (0x27A1, 0x27A1),
  (0x27B0, 0x27B0),
  (0x27BF, 0x27BF),
  (0x2934, 0x2935),
  (0x2B05, 0x2B07),
  (0x2B1B, 0x2B1C),
  (0x2B50, 0x2B50),
  (0x2B55, 0x2B55),
  (0x3030, 0x3030),
  (0x303D, 0x303D),
  (0x3297, 0x3297),
  (0x3299, 0x3299),
  (0xFE0F, 0xFE0F),
]

EMOJI_PATTERN = re.compile("[" + "".join(f"{chr(s)}-{chr(e)}" for s, e in EMOJI_RANGES) + "]")

SENSITIVE_EXACT_NAMES = {
  "id_rsa",
  "id_dsa",
  "id_ecdsa",
  "id_ed25519",
  ".netrc",
  ".htpasswd",
  "credentials.json",
}
SENSITIVE_EXTENSIONS = {".p8", ".p12", ".pfx", ".jks", ".keystore"}
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
HAS_RUBY = shutil.which("ruby") is not None
HAS_NODE = shutil.which("node") is not None
HAS_SHELLCHECK = shutil.which("shellcheck") is not None
HAS_PACKAGE_JSON = os.path.isfile("package.json")
ERROR_DETAILS = []


def escapeProp(value):
  return (
    str(value)
    .replace("%", "%25")
    .replace("\r", "%0D")
    .replace("\n", "%0A")
    .replace(":", "%3A")
    .replace(",", "%2C")
  )


def escapeData(value):
  return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emitAnnotation(level, message, filePath=None, line=None, col=None, ciMode=False):
  if ciMode:
    props = []
    if filePath is not None:
      props.append(f"file={escapeProp(filePath)}")
    if line is not None:
      props.append(f"line={line}")
    if col is not None:
      props.append(f"col={col}")
    propStr = (" " + ",".join(props)) if props else ""
    print(f"::{level}{propStr}::{escapeData(message)}")
  else:
    loc = filePath or "(repo)"
    if line is not None:
      loc = f"{loc}:{line}"
      if col is not None:
        loc = f"{loc}:{col}"
    tag = "ERROR" if level == "error" else "WARN"
    print(f"[{tag}] {loc}: {message}")

  if level == "error" and len(ERROR_DETAILS) < 50:
    loc = filePath or "(repo)"
    if line is not None:
      loc = f"{loc}:{line}"
    ERROR_DETAILS.append(f"- `{loc}`: {message}")


def listTargetFiles(mode, ciMode):
  if mode == "staged":
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
  elif mode == "tracked":
    cmd = ["git", "ls-files", "-z"]
  else:
    # ローカル既定: 追跡ファイル + .gitignore されていない未追跡ファイル
    cmd = ["git", "ls-files", "-co", "--exclude-standard", "-z"]
  try:
    res = subprocess.run(cmd, check=True, capture_output=True)
  except (subprocess.CalledProcessError, FileNotFoundError) as err:
    emitAnnotation("error", f"対象ファイル一覧の取得に失敗しました: {err}", ciMode=ciMode)
    sys.exit(2)
  return [p for p in os.fsdecode(res.stdout).split("\0") if p]


def getFileSizeAndHead(path, staged):
  if staged:
    res = subprocess.run(["git", "cat-file", "-s", f":{path}"], capture_output=True, text=True)
    size = int(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip().isdigit() else 0
    # staged 時は実ファイル読込時に先頭バイトも取れるため空で返す
    return size, None
  try:
    size = os.path.getsize(path)
  except OSError:
    size = 0
  headBytes = b""
  try:
    with open(path, "rb") as f:
      headBytes = f.read(16)
  except OSError:
    pass
  return size, headBytes


def readFileBytes(path, staged, ciMode):
  if staged:
    res = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    if res.returncode != 0:
      emitAnnotation("warning", "ステージ済み内容を読み込めませんでした", filePath=path, ciMode=ciMode)
      return None
    return res.stdout
  try:
    with open(path, "rb") as f:
      return f.read()
  except OSError as err:
    emitAnnotation("warning", f"読み込めませんでした: {err}", filePath=path, ciMode=ciMode)
    return None


def isBinaryBytes(data):
  return b"\0" in data[:BINARY_CHECK_BYTES]


def isSensitiveFileName(path, headBytes):
  base = os.path.basename(path)
  lower = base.lower()
  if lower in SENSITIVE_EXACT_NAMES:
    return True
  if lower.startswith("service-account") and lower.endswith(".json"):
    return True
  if lower.startswith("authkey_") and lower.endswith(".p8"):
    return True
  _, ext = os.path.splitext(lower)
  if ext in SENSITIVE_EXTENSIONS:
    return True
  if ext == ".key":
    if headBytes.startswith(b"PK\x03\x04"):
      return False
    return True
  if lower == ".env" or lower.startswith(".env."):
    if lower == ".env":
      return True
    segments = set(lower.split(".")[2:])
    if not (segments & ALLOWED_ENV_SUFFIXES):
      return True
  return False


def isPlaceholderMatch(matched):
  lower = matched.lower()
  return any(hint in lower for hint in PLACEHOLDER_HINTS)


def isJsoncFile(path):
  base = os.path.basename(path)
  if base in JSONC_BASENAMES:
    return True
  if (base.startswith("tsconfig") or base.startswith("jsconfig")) and base.endswith(".json"):
    return True
  return path.startswith(JSONC_DIR_PREFIXES)


def isDockerfile(base):
  return base == "Dockerfile" or base.startswith("Dockerfile.") or base.endswith(".Dockerfile")


def isRubyFile(base, ext):
  return ext in (".rb", ".podspec") or base in RUBY_SPECIAL_BASENAMES


def detectShellKind(ext, firstLine):
  if ext == ".zsh":
    return "zsh"
  if ext == ".bash":
    return "bash"
  if ext == ".sh":
    if "zsh" in firstLine:
      return "zsh"
    if "/bin/sh" in firstLine or "env sh" in firstLine:
      return "sh"
    return "bash"
  if not ext and firstLine.startswith("#!"):
    if re.search(r"(?:/|\benv\s+(?:-\S+\s+)*)zsh\b", firstLine):
      return "zsh"
    if re.search(r"(?:/|\benv\s+(?:-\S+\s+)*)bash\b", firstLine):
      return "bash"
    if re.search(r"(?:/|\benv\s+(?:-\S+\s+)*)sh\b", firstLine):
      return "sh"
  return None


def validateYaml(path, text, tmpFilePath=None):
  if "{{" in text and "}}" in text and not path.startswith(".github/"):
    return None
  if yaml is not None:
    try:
      loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
      list(yaml.compose_all(text, Loader=loader))
    except yaml.YAMLError as err:
      return str(err).splitlines()[0]
    return None
  if HAS_RUBY:
    target = tmpFilePath or path
    res = subprocess.run(
      ["ruby", "-rpsych", "-e", "Psych.parse_stream(File.read(ARGV[0]))", "--", target],
      capture_output=True,
      text=True,
    )
    if res.returncode != 0:
      msg = (res.stderr or res.stdout).strip().splitlines()
      return msg[0] if msg else "YAML syntax error"
  return None


def validateShell(targetPath, shellKind):
  errors = []
  if shutil.which(shellKind) is not None:
    res = subprocess.run([shellKind, "-n", "--", targetPath], capture_output=True, text=True)
    if res.returncode != 0:
      msg = (res.stderr or res.stdout).strip().splitlines()
      errors.append((None, None, msg[0] if msg else f"{shellKind} -n failed"))
      return errors
  if shellKind in ("sh", "bash") and HAS_SHELLCHECK:
    res = subprocess.run(
      ["shellcheck", "-s", shellKind, "-S", "error", "-f", "gcc", "--", targetPath],
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
  return errors


def validateRuby(targetPath):
  if not HAS_RUBY:
    return None
  res = subprocess.run(["ruby", "-c", "--", targetPath], capture_output=True, text=True)
  if res.returncode != 0:
    msg = (res.stderr or res.stdout).strip().splitlines()
    return msg[0] if msg else "Ruby syntax error"
  return None


def validatePlainJs(origPath, targetPath):
  if HAS_PACKAGE_JSON or not HAS_NODE or origPath.endswith(".min.js"):
    return None
  res = subprocess.run(["node", "--check", "--", targetPath], capture_output=True, text=True)
  if res.returncode != 0:
    combined = (res.stderr or res.stdout).strip()
    if "Cannot use import statement outside a module" in combined:
      return None
    lines = [ln for ln in combined.splitlines() if ln.strip()]
    return lines[-1] if lines else "JavaScript syntax error"
  return None


def validateDockerfile(text):
  errors = []
  hasFrom = False
  continuation = False
  heredocQueue = []
  escapeChar = "\\"
  sawNonComment = False

  for lineNum, rawLine in enumerate(text.splitlines(), start=1):
    if heredocQueue:
      if rawLine.strip() == heredocQueue[0]:
        heredocQueue.pop(0)
      continue

    stripped = rawLine.strip()
    if not stripped:
      continue
    if stripped.startswith("#"):
      if not sawNonComment:
        em = re.match(r"^#\s*escape\s*=\s*([\\`])\s*$", stripped, re.I)
        if em:
          escapeChar = em.group(1)
      continue

    sawNonComment = True
    if not continuation:
      token = stripped.split(None, 1)[0].upper()
      if token not in DOCKER_INSTRUCTIONS:
        errors.append((lineNum, f"未知の Dockerfile 命令です: {token}"))
      elif token == "FROM":
        hasFrom = True

    markers = re.findall(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", stripped)
    if markers:
      heredocQueue.extend(markers)
      continuation = False
    else:
      continuation = stripped.endswith(escapeChar)

  if not hasFrom:
    errors.append((1, "Dockerfile に FROM 命令が見つかりません"))
  return errors


def writeSummary(counts):
  summaryPath = os.environ.get("GITHUB_STEP_SUMMARY")
  if not summaryPath:
    return
  labels = [
    ("no_emoji", "絵文字禁止 (lint-no-emoji)", ENABLE_NO_EMOJI),
    ("secrets", "シークレット・機密ファイル検出", ENABLE_SECRET_SCAN),
    ("hygiene", "リポジトリ衛生（コンフリクト / 巨大ファイル）", ENABLE_HYGIENE),
    ("syntax", "構文チェック (JSON/TOML/YAML/Plist/Shell/Ruby/JS/Dockerfile)", ENABLE_SYNTAX),
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
      countStr = "-"
    elif counts[key] == 0:
      status = "OK"
      countStr = "0"
    else:
      status = "NG"
      countStr = str(counts[key])
    lines.append(f"| {title} | {status} | {countStr} |")
  lines.append("")
  if ERROR_DETAILS:
    lines.append("<details><summary>検出した問題の詳細（最大 50 件）</summary>")
    lines.append("")
    lines.extend(ERROR_DETAILS)
    lines.append("")
    lines.append("</details>")
    lines.append("")
  with open(summaryPath, "a", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")


def runSyntaxExternalTool(data, staged, ext, fn):
  if not staged:
    return fn(None)
  with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
    tf.write(data)
    tmpPath = tf.name
  try:
    return fn(tmpPath)
  finally:
    try:
      os.remove(tmpPath)
    except OSError:
      pass


def main():
  parser = argparse.ArgumentParser(description="Universal repository quality/security checker")
  parser.add_argument("--staged", action="store_true", help="Inspect staged git index blobs only")
  parser.add_argument("--tracked", action="store_true", help="Inspect tracked git files only")
  parser.add_argument("--ci", action="store_true", help="Emit GitHub Actions ::error annotations")
  parser.add_argument("--quiet", action="store_true", help="Print errors only")
  args = parser.parse_args()

  mode = "staged" if args.staged else ("tracked" if args.tracked else "working")
  ciMode = args.ci or (os.environ.get("GITHUB_ACTIONS") == "true")

  counts = {"no_emoji": 0, "secrets": 0, "hygiene": 0, "syntax": 0}
  maxBytes = int(MAX_FILE_SIZE_MB * 1024 * 1024)

  files = listTargetFiles(mode, ciMode)
  for path in files:
    if not args.staged and (not os.path.isfile(path) or os.path.islink(path)):
      continue

    base = os.path.basename(path)
    _, ext = os.path.splitext(base.lower())
    isLockfile = base in EXCLUDED_LOCKFILES
    isSecretAllowed = (
      base in ALLOWED_SECRET_BASENAMES
      or any(path.startswith(p) for p in ALLOWED_SECRET_PATH_PREFIXES)
    )

    size, headBytes = getFileSizeAndHead(path, args.staged)

    if ENABLE_HYGIENE and size > maxBytes:
      if ENABLE_SECRET_SCAN and not isSecretAllowed and headBytes is not None:
        if isSensitiveFileName(path, headBytes):
          emitAnnotation(
            "error",
            "機密ファイルの可能性があるファイルが追跡されています（.gitignore への追加を検討してください）",
            filePath=path,
            ciMode=ciMode,
          )
          counts["secrets"] += 1
      sizeMb = size / (1024 * 1024)
      emitAnnotation(
        "error",
        f"ファイルサイズが上限（{MAX_FILE_SIZE_MB:g}MB）を超えています（{sizeMb:.1f}MB）",
        filePath=path,
        ciMode=ciMode,
      )
      counts["hygiene"] += 1
      continue

    data = readFileBytes(path, args.staged, ciMode)
    if data is None:
      continue

    if headBytes is None:
      headBytes = data[:16]

    if ENABLE_SECRET_SCAN and not isSecretAllowed:
      if isSensitiveFileName(path, headBytes):
        emitAnnotation(
          "error",
          "機密ファイルの可能性があるファイルが追跡されています（.gitignore への追加を検討してください）",
          filePath=path,
          ciMode=ciMode,
        )
        counts["secrets"] += 1

    if ENABLE_SYNTAX and ext in (".plist", ".entitlements", ".xcprivacy"):
      try:
        plistlib.loads(data)
      except Exception as err:
        emitAnnotation("error", f"Plist 構文エラー: {err}", filePath=path, ciMode=ciMode)
        counts["syntax"] += 1

    if isBinaryBytes(data):
      continue

    strictUtf8 = True
    try:
      text = data.decode("utf-8")
    except UnicodeDecodeError:
      strictUtf8 = False
      text = data.decode("utf-8", errors="replace")

    lines = text.split("\n")

    if ENABLE_NO_EMOJI and strictUtf8 and not isLockfile and not any(path.startswith(p) for p in ALLOWED_EMOJI_PATH_PREFIXES):
      for lineNum, line in enumerate(lines, start=1):
        if IGNORE_MARKER in line:
          continue
        for match in EMOJI_PATTERN.finditer(line):
          cp = ord(match.group())
          emitAnnotation(
            "error",
            f"絵文字 U+{cp:04X} が含まれています",
            filePath=path,
            line=lineNum,
            col=match.start() + 1,
            ciMode=ciMode,
          )
          counts["no_emoji"] += 1

    if ENABLE_SECRET_SCAN and not isLockfile and not isSecretAllowed:
      for lineNum, line in enumerate(lines, start=1):
        if IGNORE_MARKER in line:
          continue
        for label, pattern in SECRET_PATTERNS:
          for match in pattern.finditer(line):
            if not isPlaceholderMatch(match.group(0)):
              emitAnnotation(
                "error",
                f"{label} の可能性があります（意図的なテストデータの場合は行に {IGNORE_MARKER} を付与してください）",
                filePath=path,
                line=lineNum,
                col=match.start() + 1,
                ciMode=ciMode,
              )
              counts["secrets"] += 1

    if ENABLE_HYGIENE:
      for lineNum, line in enumerate(lines, start=1):
        if IGNORE_MARKER in line:
          continue
        if line.startswith(CONFLICT_START) or line.startswith(CONFLICT_END):
          emitAnnotation(
            "error",
            "マージコンフリクトのマーカーが残っています",
            filePath=path,
            line=lineNum,
            ciMode=ciMode,
          )
          counts["hygiene"] += 1

    if ENABLE_SYNTAX and strictUtf8:
      firstLine = lines[0] if lines else ""
      shellKind = detectShellKind(ext, firstLine)

      if ext in (".json", ".xcstrings") and not isJsoncFile(path):
        try:
          json.loads(data.decode("utf-8-sig"))
        except json.JSONDecodeError as err:
          emitAnnotation("error", f"JSON 構文エラー: {err.msg}", filePath=path, line=err.lineno, col=err.colno, ciMode=ciMode)
          counts["syntax"] += 1
      elif ext == ".toml" and tomllib is not None:
        try:
          tomllib.loads(data.decode("utf-8-sig"))
        except tomllib.TOMLDecodeError as err:
          emitAnnotation("error", f"TOML 構文エラー: {err}", filePath=path, ciMode=ciMode)
          counts["syntax"] += 1
      elif ext in (".yml", ".yaml"):
        yamlErr = runSyntaxExternalTool(data, args.staged, ext, lambda tp: validateYaml(path, text, tp))
        if yamlErr:
          emitAnnotation("error", f"YAML 構文エラー: {yamlErr}", filePath=path, ciMode=ciMode)
          counts["syntax"] += 1
      elif shellKind is not None:
        shErrors = runSyntaxExternalTool(data, args.staged, ext or ".sh", lambda tp: validateShell(tp or path, shellKind))
        for errLine, errCol, errMsg in shErrors:
          emitAnnotation("error", f"Shell チェックエラー: {errMsg}", filePath=path, line=errLine, col=errCol, ciMode=ciMode)
          counts["syntax"] += 1
      elif isRubyFile(base, ext):
        rbErr = runSyntaxExternalTool(data, args.staged, ".rb", lambda tp: validateRuby(tp or path))
        if rbErr:
          emitAnnotation("error", f"Ruby 構文エラー: {rbErr}", filePath=path, ciMode=ciMode)
          counts["syntax"] += 1
      elif ext in (".js", ".mjs", ".cjs"):
        jsErr = runSyntaxExternalTool(data, args.staged, ext, lambda tp: validatePlainJs(path, tp or path))
        if jsErr:
          emitAnnotation("error", f"JavaScript 構文エラー: {jsErr}", filePath=path, ciMode=ciMode)
          counts["syntax"] += 1
      elif isDockerfile(base):
        for errLine, errMsg in validateDockerfile(text):
          emitAnnotation("error", f"Dockerfile チェックエラー: {errMsg}", filePath=path, line=errLine, ciMode=ciMode)
          counts["syntax"] += 1

  if ciMode:
    writeSummary(counts)
  totalErrors = sum(counts.values())
  if totalErrors > 0:
    print(f"チェック失敗: 合計 {totalErrors} 件の問題を検出しました（{counts}）")
    sys.exit(1)
  if not args.quiet:
    print(f"成功 ({len(files)} ファイル検査): 共通チェックで問題は検出されませんでした．")


if __name__ == "__main__":
  main()
