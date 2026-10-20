#!/bin/bash
# Make sure node is available. Skips everything if it already is.
# Idempotent: safe to run on every workspace boot.
set -euo pipefail

NVM_VERSION="v0.40.8"
export NVM_DIR="$HOME/.nvm"

if command -v node &>/dev/null; then
  echo "setup-node: node $(node -v) already on PATH, skipping"
  exit 0
fi

# nvm.sh is not safe under `set -u`, and its functions can return non-zero normally.
load_nvm() {
  set +eu
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  set -eu
}

# nvm may already be installed but not loaded in this non-interactive shell.
if [[ -s "$NVM_DIR/nvm.sh" ]]; then
  load_nvm
  if command -v node &>/dev/null; then
    echo "setup-node: node $(node -v) available via nvm, skipping"
    exit 0
  fi
else
  echo "setup-node: installing nvm $NVM_VERSION..."
  # PROFILE=/dev/null: ~/.zshrc is managed by this repo, don't let nvm edit it.
  curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/$NVM_VERSION/install.sh" | PROFILE=/dev/null bash
  load_nvm
fi

echo "setup-node: installing node LTS..."
set +e
nvm install --lts
status=$?
set -e
if [[ $status -ne 0 ]]; then
  echo "setup-node: nvm install --lts failed (exit $status)" >&2
  exit "$status"
fi
set +e
nvm alias default 'lts/*' >/dev/null
set -e

echo "setup-node: done (node $(node -v))"
