#!/bin/bash
# Registers Claude Code plugin marketplaces and installs plugins from dotfiles.
# Idempotent — `claude plugin marketplace add` / `install` no-op if already present.
set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$DOTFILES_DIR/config/claude/plugins.json"

if ! command -v claude &>/dev/null; then
  echo "setup-claude-plugins: claude not found, skipping"
  exit 0
fi

if ! command -v python3 &>/dev/null; then
  echo "setup-claude-plugins: python3 not found, skipping"
  exit 0
fi

while IFS=$'\t' read -r name source; do
  echo "setup-claude-plugins: ensuring marketplace $name"
  claude plugin marketplace add "$source" || true
done < <(python3 -c "
import json
data = json.load(open('$MANIFEST'))
for m in data['marketplaces']:
    print(f\"{m['name']}\t{m['source']}\")
")

while IFS= read -r plugin; do
  echo "setup-claude-plugins: ensuring plugin $plugin"
  claude plugin install "$plugin" -y || true
done < <(python3 -c "
import json
data = json.load(open('$MANIFEST'))
for p in data['plugins']:
    print(p)
")

echo "setup-claude-plugins: done"
