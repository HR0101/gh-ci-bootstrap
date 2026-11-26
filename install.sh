#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# gh-ci-bootstrap installer
#
# リポジトリ内のスクリプト・テンプレートをローカル環境へシンボリックリンクで配置する．
# （--copy 指定時はコピー配置）
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

install_file() {
  local src="$1"
  local dst="$2"
  local make_exec="${3:-false}"
  local dst_dir
  dst_dir="$(dirname "$dst")"
  mkdir -p "$dst_dir"

  if [ "$USE_COPY" = true ]; then
    if [ -L "$dst" ]; then
      rm -f "$dst"
    elif [ -e "$dst" ] && ! cmp -s "$src" "$dst"; then
      mv "$dst" "${dst}.bak.${TIMESTAMP}"
      echo "Backed up existing file: ${dst}.bak.${TIMESTAMP}"
    fi
    cp "$src" "$dst"
    if [ "$make_exec" = true ]; then
      chmod +x "$dst"
    fi
    echo "Copied: $dst <- $src"
  else
    if [ -L "$dst" ]; then
      local current_target
      current_target="$(readlink "$dst" || true)"
      if [ "$current_target" = "$src" ]; then
        echo "Already linked: $dst -> $src"
        return 0
      fi
      rm -f "$dst"
    elif [ -e "$dst" ]; then
      # 同一内容ならバックアップを作らず差し替え，差分がある場合のみ退避する
      if cmp -s "$src" "$dst"; then
        rm -f "$dst"
      else
        mv "$dst" "${dst}.bak.${TIMESTAMP}"
        echo "Backed up existing file: ${dst}.bak.${TIMESTAMP}"
      fi
    fi
    ln -s "$src" "$dst"
    echo "Linked: $dst -> $src"
  fi
}

chmod +x \
  "$REPO_DIR/bin/gh" \
  "$REPO_DIR/scripts/ci-init" \
  "$REPO_DIR/scripts/pre-push-ci-init" \
  "$REPO_DIR/hooks/pre-push"

# 1. gh ラッパー・共通スクリプト・テンプレートを配置
install_file "$REPO_DIR/bin/gh" "$HOME/.local/bin/gh" true
install_file "$REPO_DIR/scripts/ci-init" "$HOME/.local/share/gh-wrapper/ci-init" true
install_file "$REPO_DIR/scripts/pre-push-ci-init" "$HOME/.local/share/gh-wrapper/pre-push-ci-init" true
install_file "$REPO_DIR/templates/ci.yml" "$HOME/.local/share/gh-wrapper/ci.yml" false

# 2. Git テンプレートディレクトリへ pre-push スタブを配置
TEMPLATE_DIR="$(git config --global --path --get init.templateDir 2>/dev/null || true)"
if [ -z "$TEMPLATE_DIR" ]; then
  TEMPLATE_DIR="$HOME/.git-templates"
  git config --global init.templateDir "$TEMPLATE_DIR"
  echo "Configured git init.templateDir = $TEMPLATE_DIR"
fi
install_file "$REPO_DIR/hooks/pre-push" "$TEMPLATE_DIR/hooks/pre-push" true

# 3. hooks.ciOwner の確認・設定
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

# 4. PATH 優先順の確認
case ":$PATH:" in
  *":$HOME/.local/bin:"*)
    ;;
  *)
    echo ""
    echo "Note: Add \$HOME/.local/bin to the front of PATH (e.g. in ~/.zshenv):"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
    ;;
esac

echo ""
echo "Installation complete."
