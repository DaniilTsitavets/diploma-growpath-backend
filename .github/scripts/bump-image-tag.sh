#!/usr/bin/env bash
# Sets image.tag of one service in the GitOps checkout and opens a PR.
# Input env: SERVICE, TAG, IMAGE, SOURCE_URL, GH_TOKEN. Run inside the GitOps checkout.
set -euo pipefail

file="apps/values/${SERVICE}.yaml"
if [ ! -f "$file" ]; then
  echo "::notice::$file does not exist yet, skipping the tag bump"
  exit 0
fi

yq -i '.image.tag = strenv(TAG)' "$file"
if git diff --quiet; then
  echo "Tag is already $TAG"
  exit 0
fi

branch="bump/${SERVICE}-${TAG}"
git config user.name "growpath-ci[bot]"
git config user.email "growpath-ci[bot]@users.noreply.github.com"
git checkout -b "$branch"
git commit -am "Bump ${SERVICE} image to ${TAG}"
git push origin "$branch"

gh pr create --base main --head "$branch" \
  --title "Bump ${SERVICE} image to ${TAG}" \
  --body "$(printf '* Image %s:%s\n* Source commit %s' "$IMAGE" "$TAG" "$SOURCE_URL")"
