#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# gh-ci-bootstrap installer
#
# リポジトリ内のスクリプト・テンプレートをローカル環境へシンボリックリンクで配置する．
# （--copy 指定時はコピー配置．アンインストール復元のためのマニフェストを記録する）
#
# 使い方:
#   ./install.sh [--owner <github-username>] [--copy]
# =============================================================================

REPO_DIR="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
OWNER_ARG=""
USE_COPY=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --owner)
      if [ "$#" -lt 2 ]; then
        echo "Error: --owner requires a GitHub username." >&2
        exit 2
      fi
      OWNER_ARG="$2"
      shift 2
      ;;
    --copy)
      USE_COPY=true
      shift
      ;;
    -h|--help)
      cat << 'EOF'
Usage: ./install.sh [--owner <github-username>] [--copy]

Options:
  --owner <username>  Set git config --global hooks.ciOwner
  --copy              Copy files instead of creating symlinks
  -h, --help          Show this help
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
SHARE_DIR="$HOME/.local/share/gh-wrapper"
MANIFEST_FILE="$SHARE_DIR/manifest.tsv"
TMP_MANIFEST="$(mktemp -t gh-ci-manifest.XXXXXX)"
trap 'rm -f "${TMP_MANIFEST:-}"' EXIT

# 既存の非スクリプト実行バイナリ（例: ~/.local/bin/gh が本物のバイナリ）を誤って上書きしない安全チェック
GH_DST="$HOME/.local/bin/gh"
if [ -e "$GH_DST" ] && [ ! -L "$GH_DST" ]; then
  if ! grep -q "gh-wrapper\|Original gh binary" "$GH_DST" 2>/dev/null; then
    echo "Error: $GH_DST appears to be a standalone binary or custom script, not a gh-wrapper." >&2
    echo "Aborting to prevent breaking your gh installation." >&2
    exit 1
  fi
fi

is_owned_stub() {
  local p="$1"
  [ -f "$p" ] && grep -q "gh-wrapper/" "$p" 2>/dev/null
}

install_file() {
  local src="$1"
  local dst="$2"
  local make_exec="${3:-false}"
  local dst_dir
  local backup_path=""
  dst_dir="$(dirname "$dst")"
  mkdir -p "$dst_dir"

  if [ "$USE_COPY" = true ]; then
    if [ -L "$dst" ]; then
      rm -f "$dst"
    elif [ -e "$dst" ] && ! cmp -s "$src" "$dst"; then
      backup_path="${dst}.bak.${TIMESTAMP}"
      mv "$dst" "$backup_path"
      echo "Backed up existing file: $backup_path"
    fi
    cp "$src" "$dst"
    if [ "$make_exec" = true ]; then
      chmod +x "$dst"
    fi
    echo "Copied: $dst <- $src"
    printf 'copy\t%s\t%s\t%s\n' "$dst" "$src" "$backup_path" >> "$TMP_MANIFEST"
  else
    if [ -L "$dst" ]; then
      local current_target
      current_target="$(readlink "$dst" || true)"
      if [ "$current_target" = "$src" ]; then
        echo "Already linked: $dst -> $src"
        printf 'symlink\t%s\t%s\t\n' "$dst" "$src" >> "$TMP_MANIFEST"
        return 0
      fi
      rm -f "$dst"
    elif [ -e "$dst" ]; then
      if cmp -s "$src" "$dst"; then
        rm -f "$dst"
      else
        backup_path="${dst}.bak.${TIMESTAMP}"
        mv "$dst" "$backup_path"
        echo "Backed up existing file: $backup_path"
      fi
    fi
    ln -s "$src" "$dst"
    echo "Linked: $dst -> $src"
    printf 'symlink\t%s\t%s\t%s\n' "$dst" "$src" "$backup_path" >> "$TMP_MANIFEST"
  fi
}

chmod +x \
  "$REPO_DIR/bin/gh" \
  "$REPO_DIR/bin/ci-check" \
  "$REPO_DIR/scripts/ci-init" \
  "$REPO_DIR/scripts/pre-push-ci-init" \
  "$REPO_DIR/scripts/pre-commit-ci-check" \
  "$REPO_DIR/scripts/repo_check.py" \
  "$REPO_DIR/scripts/generate_dependabot.py" \
  "$REPO_DIR/scripts/sync_workflows.py" \
  "$REPO_DIR/hooks/pre-push" \
  "$REPO_DIR/hooks/pre-commit"

# 1. CLI ラッパー / ローカルチェッカー / 共通スクリプト / テンプレートを配置
install_file "$REPO_DIR/bin/gh" "$HOME/.local/bin/gh" true
install_file "$REPO_DIR/bin/ci-check" "$HOME/.local/bin/ci-check" true
install_file "$REPO_DIR/scripts/ci-init" "$SHARE_DIR/ci-init" true
install_file "$REPO_DIR/scripts/pre-push-ci-init" "$SHARE_DIR/pre-push-ci-init" true
install_file "$REPO_DIR/scripts/pre-commit-ci-check" "$SHARE_DIR/pre-commit-ci-check" true
install_file "$REPO_DIR/scripts/repo_check.py" "$SHARE_DIR/repo_check.py" true
install_file "$REPO_DIR/scripts/generate_dependabot.py" "$SHARE_DIR/generate_dependabot.py" true
install_file "$REPO_DIR/templates/ci.yml" "$SHARE_DIR/ci.yml" false
install_file "$REPO_DIR/templates/editorconfig" "$SHARE_DIR/editorconfig" false
install_file "$REPO_DIR/templates/gitattributes" "$SHARE_DIR/gitattributes" false

# 2. Git テンプレートディレクトリへ pre-push / pre-commit スタブを配置
TEMPLATE_DIR="$(git config --global --path --get init.templateDir 2>/dev/null || true)"
if [ -z "$TEMPLATE_DIR" ]; then
  TEMPLATE_DIR="$HOME/.git-templates"
  git config --global init.templateDir "$TEMPLATE_DIR"
  echo "Configured git init.templateDir = $TEMPLATE_DIR"
fi

for hook_name in pre-push pre-commit; do
  hook_dst="$TEMPLATE_DIR/hooks/$hook_name"
  if [ -e "$hook_dst" ] && [ ! -L "$hook_dst" ] && ! is_owned_stub "$hook_dst"; then
    echo "Warning: Existing custom hook at $hook_dst will be backed up (not chained)." >&2
  fi
  install_file "$REPO_DIR/hooks/$hook_name" "$hook_dst" true
done

mkdir -p "$SHARE_DIR"
if [ -f "$MANIFEST_FILE" ]; then
  # 前回インストールのバックアップパス記録は上書き時に引き継ぐ
  python3 - "$MANIFEST_FILE" "$TMP_MANIFEST" << 'PYEOF'
import sys
prev = {}
with open(sys.argv[1], "r", encoding="utf-8") as f:
    for line in f:
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 4 and parts[3]:
            prev[parts[1]] = parts[3]
rows = []
with open(sys.argv[2], "r", encoding="utf-8") as f:
    for line in f:
        parts = line.rstrip("\n").split("\t")
        while len(parts) < 4:
            parts.append("")
        if not parts[3] and parts[1] in prev:
            parts[3] = prev[parts[1]]
        rows.append("\t".join(parts))
with open(sys.argv[2], "w", encoding="utf-8") as f:
    f.write("\n".join(rows) + "\n")
PYEOF
fi
mv "$TMP_MANIFEST" "$MANIFEST_FILE"

# 3. hooks.ciOwner と core.hooksPath の確認
CURRENT_OWNER="$(git config --global --get hooks.ciOwner 2>/dev/null || true)"
if [ -n "$OWNER_ARG" ]; then
  git config --global hooks.ciOwner "$OWNER_ARG"
  echo "Configured git hooks.ciOwner = $OWNER_ARG"
elif [ -z "$CURRENT_OWNER" ]; then
  echo ""
  echo "Note: git config --global hooks.ciOwner is not set yet."
  echo "Run the following command to enable the pre-push hook for your GitHub account:"
  echo "  git config --global hooks.ciOwner <your-github-username>"
else
  echo "Kept existing git hooks.ciOwner = $CURRENT_OWNER"
fi

GLOBAL_HOOKS_PATH="$(git config --global --get core.hooksPath 2>/dev/null || true)"
if [ -n "$GLOBAL_HOOKS_PATH" ]; then
  echo "Warning: Global core.hooksPath is set to '$GLOBAL_HOOKS_PATH'." >&2
  echo "Template hooks under '$TEMPLATE_DIR/hooks' will NOT run while core.hooksPath is active." >&2
fi

# 4. PATH 優先順と gh ラッパー解決先を確認
RESOLVED_GH="$(command -v gh 2>/dev/null || true)"
if [ "$RESOLVED_GH" != "$HOME/.local/bin/gh" ]; then
  echo ""
  echo "Warning: 'gh' currently resolves to '${RESOLVED_GH:-none}', not '$HOME/.local/bin/gh'." >&2
  echo "Add \$HOME/.local/bin to the front of PATH (e.g. in ~/.zshenv):" >&2
  echo '  export PATH="$HOME/.local/bin:$PATH"' >&2
fi

echo ""
echo "Installation complete."
