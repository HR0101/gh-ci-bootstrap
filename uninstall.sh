#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# gh-ci-bootstrap uninstaller
#
# このリポジトリを指しているシンボリックリンクだけを安全に解除する．
# =============================================================================

REPO_DIR="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
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
    else
      echo "Skipped (points elsewhere): $dst -> $target"
    fi
  elif [ -e "$dst" ]; then
    echo "Skipped (not a symlink): $dst"
  fi
}

remove_if_owned_link "$HOME/.local/bin/gh" "$REPO_DIR/bin/gh"
remove_if_owned_link "$HOME/.local/bin/ci-check" "$REPO_DIR/bin/ci-check"
remove_if_owned_link "$HOME/.local/share/gh-wrapper/ci-init" "$REPO_DIR/scripts/ci-init"
remove_if_owned_link "$HOME/.local/share/gh-wrapper/pre-push-ci-init" "$REPO_DIR/scripts/pre-push-ci-init"
remove_if_owned_link "$HOME/.local/share/gh-wrapper/pre-commit-ci-check" "$REPO_DIR/scripts/pre-commit-ci-check"
remove_if_owned_link "$HOME/.local/share/gh-wrapper/repo_check.py" "$REPO_DIR/scripts/repo_check.py"
remove_if_owned_link "$HOME/.local/share/gh-wrapper/generate_dependabot.py" "$REPO_DIR/scripts/generate_dependabot.py"
remove_if_owned_link "$HOME/.local/share/gh-wrapper/ci.yml" "$REPO_DIR/templates/ci.yml"
remove_if_owned_link "$HOME/.local/share/gh-wrapper/editorconfig" "$REPO_DIR/templates/editorconfig"
remove_if_owned_link "$HOME/.local/share/gh-wrapper/gitattributes" "$REPO_DIR/templates/gitattributes"
remove_if_owned_link "$TEMPLATE_DIR/hooks/pre-push" "$REPO_DIR/hooks/pre-push"
remove_if_owned_link "$TEMPLATE_DIR/hooks/pre-commit" "$REPO_DIR/hooks/pre-commit"

echo "Uninstall complete. (git config settings were preserved.)"
