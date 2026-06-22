#!/bin/sh
input=$(cat)
model=$(echo "$input" | jq -r '.model.display_name // empty')
used=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
remaining=$(echo "$input" | jq -r '.context_window.remaining_percentage // empty')

parts=""

if [ -n "$model" ]; then
  parts="$model"
fi

if [ -n "$used" ] && [ -n "$remaining" ]; then
  ctx=$(printf "ctx: %.0f%% used" "$used")
  if [ -n "$parts" ]; then
    parts="$parts | $ctx"
  else
    parts="$ctx"
  fi
fi

echo "$parts"
