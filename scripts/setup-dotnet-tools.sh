#!/bin/bash
# Install dotnet global tools (csharp-ls for the Claude Code csharp-lsp plugin).
# Installs the .NET SDK via brew on macOS if missing. Skips if dotnet is still missing or the tool is already installed. Idempotent.
set -euo pipefail

# macOS only: Linux workspaces get the SDK from their image. Homebrew is a prerequisite, not installed here.
if ! command -v dotnet &>/dev/null && [[ "$(uname -s)" == "Darwin" ]] && command -v brew &>/dev/null; then
  echo "setup-dotnet-tools: installing .NET SDK via brew..."
  brew install --cask dotnet-sdk
fi

if ! command -v dotnet &>/dev/null; then
  echo "setup-dotnet-tools: dotnet not found, skipping"
  exit 0
fi

if dotnet tool list --global | grep -q '^csharp-ls '; then
  echo "setup-dotnet-tools: csharp-ls already installed, skipping"
  exit 0
fi

echo "setup-dotnet-tools: installing csharp-ls..."
dotnet tool install --global csharp-ls
