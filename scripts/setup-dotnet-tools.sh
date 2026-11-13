#!/bin/bash
# Install dotnet global tools (csharp-ls for the Claude Code csharp-lsp plugin).
# Skips if dotnet is missing or the tool is already installed. Idempotent.
set -euo pipefail

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
