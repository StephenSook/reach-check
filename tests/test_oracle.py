"""Differential test: a recording stub is the oracle, static reading must be a superset.

reach-check may never execute a package it reads. It can execute fixtures written here, with
every command name shimmed to a stub on a controlled PATH that records its own invocation.

The invariant is one-directional on purpose. Static reading reports what a body CONTAINS and a
run reports what it EXECUTED, so static must be a superset. A command in the trace that static
missed is an under-report, which is the failure this program exists to prevent.

Two wrong instruments were tried first, and both passed while proving nothing. `bash -x` traces
what the SHELL runs, but `find -exec grep` has find exec grep itself, so every -exec row passed
vacuously. Then a stub calling `basename` failed silently, because PATH here holds only the
stubs, and all eighteen traces came back empty behind an exit code of 0. A row that traced
nothing is now a failure rather than a pass.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

TARGET = os.environ.get(
    "REACH_ANALYSIS",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "play",
        "resources",
        "reach_analysis.py",
    ),
)
spec = importlib.util.spec_from_file_location("ra", os.path.abspath(TARGET))
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)

# `find` and `xargs` are deliberately NOT stubbed. They are symlinked to the real binaries so
# that `-exec` and a pipe really do invoke the stub beyond them.
STUBS = [
    "curl",
    "wget",
    "grep",
    "rm",
    "mktemp",
    "git",
    "chmod",
    "tar",
    "dirname",
    "readlink",
    "jq",
    "awk",
    "sed",
    "node",
    "sort",
    "head",
    "cp",
    "mv",
    "tee",
]
REAL = ["find", "xargs"]

BODIES = [
    "v=$(mktemp); curl -s https://example.com",
    'd=$(mktemp -d); rm -f "$d/x"',
    "l=$(mktemp); trap 'rm -f \"$l\"' EXIT; echo done",
    'a=$(dirname "$(readlink -f /tmp/x)"); curl "$a"',
    'find . -name "*.py" -exec grep -l TODO {} +',
    r"find . -type f -exec chmod 600 {} \;",
    'find . -name "x.py" | xargs grep -l T',
    'v=`curl -s https://x`; echo "$v"',
    'n=$((1+2)); curl "http://x/$n"',
    "(cd /tmp && rm -f junk)",
    "curl -sL https://x | tee /tmp/o | grep foo",
    'for f in a b; do cp "$f" /tmp; done',
    "case abc in a*) mv x y ;; *) cp x y ;; esac",
    "if git rev-parse --git-dir; then tar cf /tmp/a.tar .; fi",
    'v=$(git rev-parse HEAD); echo "$v" | sort | head -1',
    'x=$(mktemp) && chmod 700 "$x" && rm -f "$x"',
    'sed -n 1p /etc/hosts > /tmp/o; awk "{print}" /tmp/o',
    "command curl -s https://x; env FOO=1 wget -q https://y",
]

STUB_BODY = '#!/bin/sh\necho "${0##*/}" >> "$TRACE_LOG"\nexit 0\n'


def build_bin(bindir):
    os.makedirs(bindir)
    for name in STUBS:
        path = os.path.join(bindir, name)
        with open(path, "w") as fh:
            fh.write(STUB_BODY)
        os.chmod(path, 0o755)
    for name in REAL:
        src = shutil.which(name, path="/usr/bin:/bin")
        if src:
            os.symlink(src, os.path.join(bindir, name))


def build_sandbox(sandbox):
    os.makedirs(sandbox)
    for name, text in (("a", "x"), ("b", "x"), ("x.py", "# TODO\n")):
        with open(os.path.join(sandbox, name), "w") as fh:
            fh.write(text)


def main():
    work = tempfile.mkdtemp()
    try:
        bindir = os.path.join(work, "bin")
        sandbox = os.path.join(work, "sandbox")
        log = os.path.join(work, "trace.log")
        build_bin(bindir)
        build_sandbox(sandbox)

        fails, checked, vacuous = [], 0, 0
        for body in BODIES:
            with open(log, "w"):
                pass
            env = {"PATH": bindir, "HOME": work, "SHELL": "/bin/sh", "TRACE_LOG": log}
            try:
                subprocess.run(
                    ["/bin/bash", "-c", body],
                    cwd=sandbox,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                print("  TIMEOUT, a broken fixture rather than a finding:", body)
                fails.append((body, ["<timeout>"]))
                continue
            with open(log) as fh:
                traced = {line.strip() for line in fh if line.strip()}
            reach = ra.Reach()
            ra.scan_shell(body, reach, "oracle")
            static = {os.path.basename(x) for x in reach.execs}
            missed = traced - static
            checked += 1
            if not traced:
                vacuous += 1
            if missed:
                fails.append((body, sorted(missed)))
            print(
                "  %s traced=%-34s missed=%s"
                % (
                    "OK  " if not missed else "MISS",
                    ",".join(sorted(traced)) or "-",
                    sorted(missed) or "-",
                )
            )

        print()
        print("  %d bodies executed, %d with a command static reading missed" % (checked, len(fails)))
        print("  %d traced nothing at all, which proves nothing" % vacuous)
        for body, missed in fails:
            print("   MISSED %s   in: %s" % (missed, body))
        return 1 if (fails or vacuous) else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
