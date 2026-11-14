#!/bin/bash
# Install npm global tools (language servers for the Claude Code typescript-lsp and pyright-lsp plugins).
# Installs into ~/.npm-global (on PATH via config/.zshrc) so the tools survive nvm node switches.
# Skips if node is missing or every tool is already installed. Idempotent.
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

# binary -> npm package(s) that provide it
missing=()
[[ -x "$NPM_TOOLS_DIR/bin/typescript-language-server" ]] || missing+=(typescript-language-server typescript)
[[ -x "$NPM_TOOLS_DIR/bin/pyright-langserver" ]] || missing+=(pyright)

if [[ ${#missing[@]} -eq 0 ]]; then
  echo "setup-node-tools: all tools already installed, skipping"
  exit 0
fi

echo "setup-node-tools: installing ${missing[*]}..."
npm install --global --prefix "$NPM_TOOLS_DIR" "${missing[@]}"
