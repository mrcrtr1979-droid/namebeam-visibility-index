#!/bin/sh
set -eu
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != --dry-run ]; }; then
  echo "Usage: run.sh [--dry-run]" >&2
  exit 2
fi
if [ -z "${OPENAI_API_KEY:-}" ] || [ -z "${ANTHROPIC_API_KEY:-}" ] ||
   [ -z "${PERPLEXITY_API_KEY:-}" ] || [ -z "${GEMINI_API_KEY_FREE:-}" ]; then
  echo "Required secret names: OPENAI_API_KEY ANTHROPIC_API_KEY PERPLEXITY_API_KEY GEMINI_API_KEY_FREE" >&2
  exit 2
fi
if [ "${1:-}" = --dry-run ]; then
  python -c 'import requests; import boto3'
  echo "Dry-run passed; no collector or upload started."
  exit 0
fi
case "${COLLECTOR_ARCHIVE_BUCKET:-}" in
  gs://?*) ;;
  *) echo "Set COLLECTOR_ARCHIVE_BUCKET to a private gs:// bucket." >&2; exit 2 ;;
esac
cd "${COLLECTOR_ROOT:-/app}"
python scripts/e1_runner.py
test -n "$(find corpus/e1 -type f -name 'NB-CZ-API_*.json' -print -quit)"
archive=$(mktemp)
trap 'rm -f "$archive"' EXIT
set -- corpus/e1
if [ -d corpus/e1_q2 ]; then set -- "$@" corpus/e1_q2; fi
tar -czf "$archive" "$@"
stamp=$(date -u '+%Y-%m-%dT%H%M%SZ')
gcloud storage cp --if-generation-match=0 "$archive" "${COLLECTOR_ARCHIVE_BUCKET%/}/collector/${CLOUD_RUN_EXECUTION:-manual}-$stamp.tar.gz"
