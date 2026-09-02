#!/usr/bin/env bash
set -euo pipefail
DEPTH="${1:-3}"
if [ -d "src" ]; then
  echo "scanning"
  shell-only-tool --depth "$DEPTH"
  find . -type f -name '*.py' | head -5
else
  printf '%s\n' "no src"
  exit 0
fi
for f in *.txt; do
  test -f "$f" && grep -q needle "$f"
done

echo marker > /tmp/reach-check-fixture-outside
echo local > out/local.log
