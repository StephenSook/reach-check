#!/usr/bin/env bash
set -euo pipefail
root="$1"
kill -0 $$ 2>/dev/null
# A step's stdout is cut at 65536 bytes, so the list is clipped here (the apostrophe matters).
final=$(python3 -B -c '
import json, subprocess, sys
paths = [line.rstrip("\n") for line in open(sys.argv[1]) if line.strip()]
out = []
for path in paths:
    out.append(path)
subprocess.run(["quoted-body-tool", "--check"], check=False)
print(json.dumps(out))
' "$root")
ROUTE_RE='export[[:space:]]+(GET|POST|PUT)|\.(get|post|route)[[:space:]]*\([[:space:]]*[`"'"'"']'
grep -E -- "$ROUTE_RE" "$root" >/dev/null || true
printf '%s\n' "$final"
