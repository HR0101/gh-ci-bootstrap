#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# gh-ci-bootstrap uninstaller
#
# install.sh が記録したマニフェスト（および所有する symlink）に基づき安全に削除し，
# 退避していた .bak ファイルがあれば復元する．
# =============================================================================

REPO_DIR="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SHARE_DIR="$HOME/.local/share/gh-wrapper"
MANIFEST_FILE="$SHARE_DIR/manifest.tsv"
TEMPLATE_DIR="$(git config --global --path --get init.templateDir 2>/dev/null || echo "$HOME/.git-templates")"

remove_if_owned_link() {
  local dst="$1"
  local expected_src="$2"
  if [ -L "$dst" ]; then
    local target
    target="$(readlink "$dst" || true)"
    if [ "$target" = "$expected_src" ]; then
      rm -f "$dst"
      echo "Removed symlink: $dst"
      return 0
    else
      echo "Skipped (points elsewhere): $dst -> $target"
      return 1
    fi
  elif [ -e "$dst" ]; then
    echo "Skipped (not a symlink): $dst"
    return 1
  fi
  return 0
}

if [ -f "$MANIFEST_FILE" ]; then
  while IFS=$'\t' read -r mode dst src backup; do
    [ -z "${dst:-}" ] && continue
    removed=false
    if [ "$mode" = "symlink" ]; then
      if remove_if_owned_link "$dst" "$src"; then
        removed=true
      fi
    elif [ "$mode" = "copy" ]; then
      if [ -f "$dst" ] && [ ! -L "$dst" ]; then
        if [ -f "$src" ] && cmp -s "$src" "$dst"; then
          rm -f "$dst"
          echo "Removed copy: $dst"
          removed=true
        else
          echo "Skipped modified copy: $dst"
        fi
      fi
    fi

    if [ "$removed" = true ] && [ -n "${backup:-}" ] && [ -e "$backup" ] && [ ! -e "$dst" ] && [ ! -L "$dst" ]; then
      mv "$backup" "$dst"
      echo "Restored backup: $backup -> $dst"
    fi
  done < "$MANIFEST_FILE"
  rm -f "$MANIFEST_FILE"
else
  remove_if_owned_link "$HOME/.local/bin/gh" "$REPO_DIR/bin/gh" || true
  remove_if_owned_link "$HOME/.local/bin/ci-check" "$REPO_DIR/bin/ci-check" || true
  remove_if_owned_link "$SHARE_DIR/ci-init" "$REPO_DIR/scripts/ci-init" || true
  remove_if_owned_link "$SHARE_DIR/pre-push-ci-init" "$REPO_DIR/scripts/pre-push-ci-init" || true
  remove_if_owned_link "$SHARE_DIR/pre-commit-ci-check" "$REPO_DIR/scripts/pre-commit-ci-check" || true
  remove_if_owned_link "$SHARE_DIR/repo_check.py" "$REPO_DIR/scripts/repo_check.py" || true
  remove_if_owned_link "$SHARE_DIR/generate_dependabot.py" "$REPO_DIR/scripts/generate_dependabot.py" || true
  remove_if_owned_link "$SHARE_DIR/ci.yml" "$REPO_DIR/templates/ci.yml" || true
  remove_if_owned_link "$SHARE_DIR/editorconfig" "$REPO_DIR/templates/editorconfig" || true
  remove_if_owned_link "$SHARE_DIR/gitattributes" "$REPO_DIR/templates/gitattributes" || true
  remove_if_owned_link "$TEMPLATE_DIR/hooks/pre-push" "$REPO_DIR/hooks/pre-push" || true
  remove_if_owned_link "$TEMPLATE_DIR/hooks/pre-commit" "$REPO_DIR/hooks/pre-commit" || true
fi

echo "Uninstall complete. (git config settings were preserved.)"
