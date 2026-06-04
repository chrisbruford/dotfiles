#!/bin/bash
# Dotfiles installer for Coder engineer workspaces.
# Runs on every workspace start and on manual refresh — must be idempotent.
set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Git identity ---
git config --global user.name  "Chris Bruford"
git config --global user.email "chris.bruford@liberis.com"
git config --global core.editor "vim"
git config --global pull.rebase false
git config --global init.defaultBranch main
git config --global push.autoSetupRemote true

# --- Zsh + oh-my-zsh + powerlevel10k ---
"$DOTFILES_DIR/scripts/setup-zsh.sh"

# --- Place zsh config (overwrite on each run so updates land automatically) ---
cp "$DOTFILES_DIR/config/.zshrc" "$HOME/.zshrc"
cp "$DOTFILES_DIR/.p10k.zsh"    "$HOME/.p10k.zsh"

# --- Switch interactive bash sessions to zsh ---
if ! grep -q '# dotfiles: exec zsh' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc << 'EOF'

# dotfiles: exec zsh
[[ -z "$ZSH_VERSION" && $- == *i* ]] && command -v zsh &>/dev/null && exec zsh
EOF
fi

# --- Bash fallback aliases (used if zsh setup fails) ---
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
