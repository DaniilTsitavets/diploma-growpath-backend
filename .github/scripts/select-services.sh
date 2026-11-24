#!/usr/bin/env bash
# Writes the services to build as a JSON array to $GITHUB_OUTPUT (key "services").
# Input env: INPUT (service name or "all", manual runs), BEFORE (head of the branch before the push).
set -euo pipefail

all='["commerce","entitlement","plant-care","expert-consultation"]'

if [ "$GITHUB_EVENT_NAME" = workflow_dispatch ]; then
  if [ "$INPUT" = all ]; then
    picked="$all"
  else
    picked=$(jq -cn --arg service "$INPUT" '[$service]')
  fi
elif ! git cat-file -e "${BEFORE}^{commit}" 2>/dev/null; then
  # A new branch has no previous head to compare with.
  picked="$all"
else
  changed=$(git diff --name-only "$BEFORE" HEAD)
  if echo "$changed" | grep -qE '^(libs/|pyproject\.toml$|uv\.lock$)'; then
    picked="$all"
  else
    picked=$(jq -cn --argjson all "$all" --arg changed "$changed" '
      $all | map(select(. as $s | $changed | split("\n") | any(startswith("services/" + $s + "/"))))')
  fi
fi

echo "services=$picked" >> "$GITHUB_OUTPUT"
echo "Building: $picked"
