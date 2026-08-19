#!/bin/bash
# Sets up user-level Codex CLI configuration from dotfiles.
# Idempotent — safe to run on every workspace start.
set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_SRC="$DOTFILES_DIR/config/codex"
CODEX_DIR="$HOME/.codex"

mkdir -p "$CODEX_DIR/agents" "$CODEX_DIR/skills"

# --- AGENTS.md ---
cp "$CODEX_SRC/AGENTS.md" "$CODEX_DIR/AGENTS.md"

# --- Agents ---
cp "$CODEX_SRC/agents/"*.toml "$CODEX_DIR/agents/"

# --- Skills shared with Claude Code: symlink rather than duplicate ---
for skill in azure-ad-sso liberis-brand pr-merge-ready tracey; do
  ln -sfn "$HOME/.claude/skills/$skill" "$CODEX_DIR/skills/$skill"
done

# --- Codex-only skills (deliver variant, tmux-deliver-agent): copy directly ---
rsync -a "$CODEX_SRC/skills/" "$CODEX_DIR/skills/"

echo "codex: configuration applied"
