#!/usr/bin/env bash
# Uploads a built SPA (Vite `dist/`) to the frontend bucket that CloudFront serves.
set -euo pipefail

dist=${1:?usage: deploy-frontend.sh <path to the built dist folder>}
[ -f "$dist/index.html" ] || { echo "no index.html in $dist: run the frontend build first" >&2; exit 1; }

export AWS_ENDPOINT_URL=${AWS_ENDPOINT_URL_HOST:-http://localhost:4566}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test

aws s3 sync "$dist" s3://growpath-frontend --delete
