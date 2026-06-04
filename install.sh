#!/bin/bash
# Dotfiles installer for Coder engineer workspaces.
# Runs on every workspace start and on manual refresh — must be idempotent.
set -euo pipefail

# --- Git identity ---
git config --global user.name  "Chris Bruford"
git config --global user.email "chris.bruford@liberis.com"
git config --global core.editor "vim"
git config --global pull.rebase false
git config --global init.defaultBranch main
git config --global push.autoSetupRemote true

# --- Shell aliases ---
# Guarded by a sentinel comment so the block is added at most once.
if ! grep -q '# dotfiles: shell aliases' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc << 'EOF'

# dotfiles: shell aliases
alias ll='ls -lah --color=auto'
alias la='ls -A --color=auto'
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

echo "dotfiles: applied successfully"
