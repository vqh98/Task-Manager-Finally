#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/export.jsonl" >&2
  exit 2
fi
if [[ -z "${TGCLOUD_TOKEN:-}" ]]; then
  echo "TGCLOUD_TOKEN is required" >&2
  exit 2
fi

input=$1
total=$(wc -l < "$input")
current=0
while IFS= read -r payload; do
  current=$((current + 1))
  if ! npx tgcloud run lib/import-data "$payload" >/tmp/tgcloud-import-last.log 2>&1; then
    echo "batch $current/$total failed" >&2
    sed -n '1,120p' /tmp/tgcloud-import-last.log >&2
    exit 1
  fi
  if (( current % 20 == 0 || current == total )); then
    echo "imported batches: $current/$total"
  fi
done < "$input"
