import json
import os
import shutil
import subprocess
import sys


def arg(i, fallback):
    if len(sys.argv) > i:
        v = sys.argv[i]
        if v and not v.startswith("$"):
            return v
    return fallback


target = arg(1, "all")
offline = arg(2, "false").lower() in ("1", "true", "yes")
root = os.path.expanduser(arg(3, "~/.rote/flows"))

rote = shutil.which("rote")
if rote is None:
    for cand in ("~/.local/bin/rote", "~/.cargo/bin/rote"):
        c = os.path.expanduser(cand)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            rote = c
            break

if offline or rote is None:
    print(
        json.dumps(
            {
                "ok": True,
                "available": False,
                "verdicts": {},
                "warning": "skipped: offline was requested"
                if offline
                else "skipped: rote is not on PATH here, so its own verdict is unavailable",
            }
        )
    )
    raise SystemExit(0)


def store_refs():
    refs = []
    if not os.path.isdir(root):
        return refs
    for owner in sorted(os.listdir(root)):
        od = os.path.join(root, owner)
        if owner.startswith(".") or not os.path.isdir(od):
            continue
        for name in sorted(os.listdir(od)):
            if name.startswith("."):
                continue
            if os.path.isfile(os.path.join(od, name, "manifest.json")):
                refs.append(owner + "/" + name)
    return refs


# Only ask rote about what the caller asked about. Inspecting every installed package when the
# caller named one is what made this step exceed its own timeout.
if target in ("", "all"):
    refs = store_refs()
    note = None
elif "/" in target and not target.startswith((".", "/", "~")):
    refs = [target]
    note = None
else:
    refs = []
    note = "skipped: a local path has no published reference to inspect, so rote has no verdict to give for it"

if note is not None:
    print(json.dumps({"ok": True, "available": False, "verdicts": {}, "warning": note}))
    raise SystemExit(0)

# Keep the whole step inside its declared budget no matter how many packages are installed.
# Per-call time alone does not bound the step: N packages times a 4s floor is unbounded, and a
# store with hundreds of Plays timed the whole step out. Bound the COUNT as well as the time.
MAX_REFS = 6
skipped_refs = 0
if len(refs) > MAX_REFS:
    skipped_refs = len(refs) - MAX_REFS
    refs = refs[:MAX_REFS]
budget = max(4, min(18, int(20 / max(1, len(refs))) if refs else 18))

verdicts = {}
for ref in refs:
    try:
        r = subprocess.run(
            [rote, "play", "inspect", ref, "--json"],
            capture_output=True,
            text=True,
            timeout=budget,
            check=False,
        )
        d = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        verdicts[ref] = {"available": False, "warning": "inspect unavailable: %s" % str(exc)[:60]}
        continue
    if not d.get("ok"):
        err = (d.get("error") or {}).get("kind") or "inspect failed"
        verdicts[ref] = {"available": False, "warning": str(err)[:80]}
        continue
    ex = ((d.get("data") or {}).get("play_inspect") or {}).get("execution") or {}
    verdicts[ref] = {
        "available": True,
        "play_run_eligible": ex.get("play_run_eligible"),
        "blockers": ex.get("blockers") or [],
        "privileged_access": ex.get("privileged_access"),
    }

payload = {"ok": True, "available": True, "count": len(verdicts), "verdicts": verdicts}
if skipped_refs:
    payload["warning"] = (
        "rote's own verdict was read for the first %d package(s) only; %d more were skipped so the "
        "step stays inside its time budget. Ask for one package by name to see its verdict."
        % (len(verdicts), skipped_refs)
    )
print(json.dumps(payload))
