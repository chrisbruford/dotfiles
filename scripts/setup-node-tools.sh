#!/bin/bash
# Install npm global tools (typescript-language-server for the Claude Code typescript-lsp plugin).
# Installs into ~/.npm-global (on PATH via config/.zshrc) so the tools survive nvm node switches.
# Skips if node is missing or the tool is already installed. Idempotent.
set -euo pipefail

NPM_TOOLS_DIR="$HOME/.npm-global"

# node may only be reachable via nvm, which isn't loaded in this non-interactive shell.
if ! command -v npm &>/dev/null && [[ -s "$HOME/.nvm/nvm.sh" ]]; then
  set +eu
  # shellcheck disable=SC1091
  . "$HOME/.nvm/nvm.sh"
  set -eu
fi

if ! command -v npm &>/dev/null; then
  echo "setup-node-tools: npm not found, skipping"
  exit 0
fi

if [[ -x "$NPM_TOOLS_DIR/bin/typescript-language-server" ]]; then
  echo "setup-node-tools: typescript-language-server already installed, skipping"
  exit 0
fi

echo "setup-node-tools: installing typescript-language-server..."
npm install --global --prefix "$NPM_TOOLS_DIR" typescript-language-server typescript
