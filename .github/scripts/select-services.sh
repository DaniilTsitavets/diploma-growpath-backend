#!/usr/bin/env bash
# Prints the services to build as a JSON array into $GITHUB_OUTPUT (key "services").
# Input env: EVENT (workflow_dispatch or workflow_run), INPUT (dispatch services list).
set -euo pipefail

all='["commerce","entitlement","plant-care","expert-consultation"]'

if [ "$EVENT" = workflow_dispatch ]; then
  if [ "$INPUT" = all ]; then
    picked="$all"
  else
    picked=$(jq -cn --argjson all "$all" --arg input "$INPUT" '
      ($input | split(",") | map(gsub(" "; ""))) as $wanted
      | $all | map(select(. as $s | $wanted | index($s)))')
  fi
else
  # The squash commit has exactly one parent, so HEAD^..HEAD is the merged change.
  changed=$(git diff --name-only HEAD^ HEAD)
  if echo "$changed" | grep -qE '^(libs/|pyproject\.toml$|uv\.lock$)'; then
    picked="$all"
  else
    picked=$(jq -cn --argjson all "$all" --arg changed "$changed" '
      $all | map(select(. as $s | $changed | split("\n") | any(startswith("services/" + $s + "/"))))')
  fi
fi

echo "services=$picked" >> "$GITHUB_OUTPUT"
echo "Building: $picked"
