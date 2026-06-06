#!/bin/bash
# Install zsh, oh-my-zsh, powerlevel10k, and zsh-autosuggestions.
# Idempotent: safe to run on every workspace boot.
set -euo pipefail

if ! command -v zsh &>/dev/null; then
  echo "setup-zsh: installing zsh..."
  sudo apt-get update -qq && sudo apt-get install -y -qq zsh
fi

if ! command -v tmux &>/dev/null; then
  echo "setup-zsh: installing tmux..."
  sudo apt-get update -qq && sudo apt-get install -y -qq tmux
fi

if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
  echo "setup-zsh: installing oh-my-zsh..."
  RUNZSH=no CHSH=no sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
fi

P10K_DIR="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/themes/powerlevel10k"
if [[ ! -d "$P10K_DIR" ]]; then
  echo "setup-zsh: installing powerlevel10k..."
  git clone --depth=1 https://github.com/romkatv/powerlevel10k.git "$P10K_DIR"
fi

AUTOSUGGEST_DIR="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-autosuggestions"
if [[ ! -d "$AUTOSUGGEST_DIR" ]]; then
  echo "setup-zsh: installing zsh-autosuggestions..."
  git clone https://github.com/zsh-users/zsh-autosuggestions "$AUTOSUGGEST_DIR"
fi

echo "setup-zsh: done"
