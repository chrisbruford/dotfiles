#!/bin/bash
# Installs Claude Code plugins from dotfiles, adding each plugin's marketplace if missing.
# Idempotent — `claude plugin install` no-ops if already installed. Needs Claude Code >= 2.1.292 (--marketplace).
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

# Captured first so a plugin naming an unlisted marketplace fails the script under set -e.
pairs=$(python3 -c "
import json
data = json.load(open('$MANIFEST'))
sources = {m['name']: m['source'] for m in data['marketplaces']}
for p in data['plugins']:
    name, marketplace = p.split('@')
    print(f'{name}\t{sources[marketplace]}')
")

while IFS=$'\t' read -r plugin source; do
  echo "setup-claude-plugins: ensuring plugin $plugin from $source"
  claude plugin install "$plugin" --marketplace "$source" -y || true
done <<< "$pairs"

echo "setup-claude-plugins: done"
