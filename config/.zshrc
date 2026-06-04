# Enable Powerlevel10k instant prompt. Must stay close to the top of ~/.zshrc.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="powerlevel10k/powerlevel10k"

plugins=(
  git
  zsh-autosuggestions
)

source $ZSH/oh-my-zsh.sh

# Workspace PATH
export PATH="$HOME/.local/bin:$PATH"
export PATH="$HOME/.npm-global/bin:$PATH"

# Aliases
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

[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh
