#!/bin/bash
# Dotfiles installer for Coder engineer workspaces (Linux) and local macOS machines.
# Runs on every workspace start and on manual refresh — must be idempotent.
set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Git identity ---
git config --global user.name  "Chris Bruford"
git config --global user.email "chrisbruford@gmail.com"
git config --global core.editor "vim"
git config --global pull.rebase false
git config --global init.defaultBranch main
git config --global push.autoSetupRemote true

# --- Zsh + oh-my-zsh + powerlevel10k ---
"$DOTFILES_DIR/scripts/setup-zsh.sh"

# Set zsh as the login shell (idempotent)
ZSH_BIN="$(command -v zsh)"
if [[ "$SHELL" != "$ZSH_BIN" ]]; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sudo chsh -s "$ZSH_BIN" "$(whoami)"
  else
    sudo usermod -s "$ZSH_BIN" "$(whoami)"
  fi
fi

# --- Place zsh config (overwrite on each run so updates land automatically) ---
cp "$DOTFILES_DIR/config/.zshrc"        "$HOME/.zshrc"
cp "$DOTFILES_DIR/config/.zsh_aliases"  "$HOME/.zsh_aliases"
cp "$DOTFILES_DIR/.p10k.zsh"            "$HOME/.p10k.zsh"

# --- Switch interactive bash sessions to zsh ---
if ! grep -q '# dotfiles: exec zsh' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc << 'EOF'

# dotfiles: exec zsh
[[ -z "$ZSH_VERSION" && $- == *i* ]] && command -v zsh &>/dev/null && exec zsh
EOF
fi

# --- Bash fallback aliases (used if zsh setup fails) ---
if ! grep -q '# dotfiles: shell aliases' ~/.bashrc 2>/dev/null; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    LS_FLAGS_LONG="-lahG"
    LS_FLAGS_ALL="-AG"
  else
    LS_FLAGS_LONG="-lah --color=auto"
    LS_FLAGS_ALL="-A --color=auto"
  fi
  cat >> ~/.bashrc << EOF

# dotfiles: shell aliases
alias ll='ls $LS_FLAGS_LONG'
alias la='ls $LS_FLAGS_ALL'
alias gs='git status --short'
alias gd='git diff'
alias gp='git push'
alias gl='git log --oneline --graph --decorate -20'
alias gco='git checkout'
alias gcb='git checkout -b'
alias gst='git stash'
alias gpop='git stash pop'
EOF
fi

# --- Vim preferences ---
cat > ~/.vimrc << 'VIMRC'
set number
set expandtab
set tabstop=2
set shiftwidth=2
set autoindent
set hlsearch
set incsearch
syntax on
VIMRC

# --- Claude Code user-level configuration ---
if command -v claude &>/dev/null; then
  "$DOTFILES_DIR/scripts/setup-claude.sh"
  "$DOTFILES_DIR/scripts/setup-claude-plugins.sh"
else
  echo "dotfiles: claude not found, skipping Claude Code setup"
fi

# --- Codex CLI user-level configuration ---
if command -v codex &>/dev/null; then
  "$DOTFILES_DIR/scripts/setup-codex.sh"
else
  echo "dotfiles: codex not found, skipping Codex setup"
fi

echo "dotfiles: applied successfully"
