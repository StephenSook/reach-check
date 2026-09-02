#!/usr/bin/env python3
"""Hermetic tests for the reach extractor.

Every case runs against a fixture package in tests/fixtures, so this suite needs
nothing installed and no packages pulled. Run it with:

    python3 tests/test_reach.py

It is deliberately dependency free and runs on python 3.9 and newer, which is the
floor a stranger on a stock macOS actually has.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
EXTRACTOR = os.path.join(ROOT, "play", "resources", "reach_analysis.py")

spec = importlib.util.spec_from_file_location("reach_analysis", EXTRACTOR)
mod = importlib.util.module_from_spec(spec)
sys.modules["reach_analysis"] = mod
spec.loader.exec_module(mod)

FAILURES = []
CHECKS = 0


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print("  pass  " + label)
    else:
        print("  FAIL  " + label + ("  -> " + str(detail) if detail else ""))
        FAILURES.append(label)


def analyze(name):
    return mod.analyze(os.path.join(FIX, name), name)


print("reach extractor, hermetic fixtures")
print(
    "python",
    sys.version.split()[0],
    "| stdlib via",
    "api" if hasattr(sys, "stdlib_module_names") else "sysconfig-fallback",
)
print()

print("loop-variable: a binary reached only through a python loop variable")
r = analyze("loop-variable")
check("finds both tools bound in a module-level list", {"alpha-tool", "beta-tool"} <= set(r["reached"]), r["reached"])
check(
    "reports them as undeclared",
    {"alpha-tool", "beta-tool"} <= set(r["undeclared_but_reached"]),
    r["undeclared_but_reached"],
)
check("still records the declared interpreter", "python3" in r["reached"], r["reached"])
check(
    "an argv list bound to a name yields the command, not its arguments",
    "listing-tool" in r["reached"] and not any(c in r["reached"] for c in ("-axo", "pid=,rss=")),
    r["reached"],
)

print("shell-nested: a shell body that writes and nests an interpreter")
r = analyze("shell-nested")
check("finds shell command heads across separators", {"sleep", "mkdir", "tee"} <= set(r["reached"]), r["reached"])
check("recurses into the nested python3 -c", "python3" in r["reached"], r["reached"])
check("records a write inside the working directory", len(r["writes_cwd"]) >= 1, r["writes_cwd"])
check("invents no write outside the working directory", r["writes_outside_cwd"] == [], r["writes_outside_cwd"])

print("resource-body: a body behind rote's exec shim")
r = analyze("resource-body")
check("follows the @resource reference to the real body", "gamma-tool" in r["reached"], r["reached"])
check(
    "reads environment variables by name",
    {"FIXTURE_TOKEN", "FIXTURE_HOME"} <= set(r["env_vars_read"]),
    r["env_vars_read"],
)
check(
    "does not attribute the shim's own imports to the Play",
    "_frozen_importlib" not in r["third_party_imports"] + r.get("sibling_modules", []),
    r["third_party_imports"],
)

print("adapter-browser: non-process steps are reach too")
r = analyze("adapter-browser")
check("adapter step with no type key is found", r["adapters_reached"] == ["adapter/example"], r["adapters_reached"])
check("browser steps are flagged", r["needs_browser"] is True, r["step_kinds"])

print("authored-frontmatter: a package with no manifest.json yet")
r = analyze("authored-frontmatter")
check("reads the package from main.ts frontmatter", "frontmatter" in r["read_from"], r["read_from"])
check("parses a YAML block scalar body", "delta-tool" in r["reached"], r["reached"])
check("does not mistake depends_on entries for argv", "probe" not in r["reached"], r["reached"])
check("reads declared tools from deps.toml", r["declared"] == ["python3"], r["declared"])

print("nested-resources: sibling modules in a subdirectory")
r = analyze("nested-resources")
check("follows a resource in a subdirectory", "nested-tool" in r["reached"], r["reached"])
check(
    "treats a nested sibling module as local, not missing",
    r["missing_modules"] == [] and "helper" in r.get("sibling_modules", []),
    (r["missing_modules"], r.get("sibling_modules")),
)

print("shell-script-resource: a real script, not a one-liner")
r = analyze("shell-script-resource")
check("finds the real commands", {"shell-only-tool", "find", "grep"} <= set(r["reached"]), r["reached"])
check(
    "reports no shell grammar words",
    not ({"if", "then", "fi", "else", "for", "do", "done", "{", "}"} & set(r["reached"])),
    r["reached"],
)
check("reports no shell builtins", not ({"echo", "printf", "set", "exit", "test"} & set(r["reached"])), r["reached"])
check("reports no assignments or globs", not any(("=" in c) or ("*" in c) for c in r["reached"]), r["reached"])
check("does not report a for-loop variable as a command", "f" not in r["reached"], r["reached"])
check(
    "a redirect to an absolute path is outside the working directory",
    any(w["target"] == "/tmp/reach-check-fixture-outside" for w in r["writes_outside_cwd"]),
    r["writes_outside_cwd"],
)
check(
    "a redirect to a relative path stays inside the working directory",
    any(w["target"] == "out/local.log" for w in r["writes_cwd"]),
    r["writes_cwd"],
)

print("escaping-resource: a package that points outside itself")
r = analyze("escaping-resource")
check(
    "refuses to read outside the package",
    any("escapes the package" in u.get("reason", "") for u in r["unanalysed_bodies"]),
    r["unanalysed_bodies"],
)
check("reads no content from the escape target", "root" not in r["reached"], r["reached"])

print("javascript-body: a language this tool does not read")
r = analyze("javascript-body")
check(
    "says plainly that the body was not analysed",
    any("javascript" in u.get("reason", "") for u in r["unanalysed_bodies"]),
    r["unanalysed_bodies"],
)
check("does not silently claim there is no reach", len(r["unanalysed_bodies"]) >= 1, r["unanalysed_bodies"])

print("python-writes: writes performed from a Python body")
r = analyze("python-writes")
cwd_targets = [w["target"] for w in r["writes_cwd"]]
out_targets = [w["target"] for w in r["writes_outside_cwd"]]
check("finds a write inside the working directory", "out/report.json" in cwd_targets, cwd_targets)
check("finds a directory creation inside the working directory", "out/nested" in cwd_targets, cwd_targets)
check("finds a copy that leaves the working directory", "/etc/somewhere-else" in out_targets, out_targets)
check("finds a pathlib write to a home path", "~/.ssh/authorized_keys" in out_targets, out_targets)
check(
    "does not treat a read-mode open as a write",
    not any(t == "read-only.txt" for t in cwd_targets + out_targets),
    cwd_targets + out_targets,
)

print("shell-true-and-aliases: a command line, and imports under another name")
r = analyze("shell-true-and-aliases")
check("shell=True is read as a command line, not an argv list", {"curl", "sh"} <= set(r["reached"]), r["reached"])
check("an aliased subprocess import is still recognised", "aliased-tool" in r["reached"], r["reached"])
check("an aliased os import is still recognised for env vars", "ALIASED_ENV" in r["env_vars_read"], r["env_vars_read"])

print("malformed-manifest: a hostile package must not take the run down")
r = analyze("malformed-manifest")
check("degrades instead of raising", isinstance(r, dict) and r.get("ref") == "malformed-manifest", r)
check("says why it could not be read", len(r.get("notes", [])) > 0, r.get("notes"))

print("embedded-bodies: a body inside a body, and a heredoc")
r = analyze("embedded-bodies")
check("finds the outer shell commands", {"head-tool", "tail-tool"} <= set(r["reached"]), r["reached"])
check("reads the embedded python body", "embedded-tool" in r["reached"], r["reached"])
check(
    "invents nothing from python source lines",
    not ({"import", "subprocess.run", "os.path.join"} & set(r["reached"])),
    r["reached"],
)
check(
    "invents nothing from heredoc text",
    not ({"MARK", "some", "heredoc", "body", "words"} & set(r["reached"])),
    r["reached"],
)
check("recurses into subprocess.run(['sh', '-c', ...])", "via-subprocess-tool" in r["reached"], r["reached"])

print("write-targets: every target, with the right scope")
r = analyze("write-targets")
out = [w["target"] for w in r["writes_outside_cwd"]]
cwd = [w["target"] for w in r["writes_cwd"]]
check("records a removal target that leaves the working directory", "/etc/fixture-thing" in out, out)
check("still records the local target of the same command", "out" in cwd, cwd)
check("reads a key=value write target", "/etc/fixture-passwd" in out, out)
pairs_cwd = [(w["command"], w["target"]) for w in r["writes_cwd"]]
pairs_out = [(w["command"], w["target"]) for w in r["writes_outside_cwd"]]
check(
    "does not report the same write twice",
    len(pairs_cwd) == len(set(pairs_cwd)) and len(pairs_out) == len(set(pairs_out)),
    (pairs_cwd, pairs_out),
)
check("a device sink is not reported as a filesystem write", "/dev/zero" not in out, out)

print("dotted-imports: find_spec must only ever see a top-level name")
r = analyze("dotted-imports")
all_mods = r["third_party_imports"] + r.get("sibling_modules", []) + r["missing_modules"]
check("no dotted name survives to the module check", not any("." in mo for mo in all_mods), all_mods)
check("dotted stdlib imports resolve without being reported missing", r["missing_modules"] == [], r["missing_modules"])

print("wrapped-commands: substitutions and forwarding wrappers")
r = analyze("wrapped-commands")
check("reads inside a command substitution", "subst-tool" in r["reached"], r["reached"])
check("reads inside backticks", "backtick-tool" in r["reached"], r["reached"])
check("follows a forwarding builtin to the real command", "fwd-tool" in r["reached"], r["reached"])
check("follows env past its assignments", "env-tool" in r["reached"], r["reached"])
check("does not report the forwarding builtin itself", not ({"command", "exec"} & set(r["reached"])), r["reached"])

print("large store: a store with hundreds of Plays must still complete")
_store = tempfile.mkdtemp(prefix="reachscale-")
for _i in range(90):
    _pkg = os.path.join(_store, "owner%d" % _i, "p%d" % _i)
    os.makedirs(_pkg)
    with open(os.path.join(_pkg, "manifest.json"), "w", encoding="utf-8") as _f:
        json.dump(
            {
                "name": "p%d" % _i,
                "description": "x",
                "metadata": {},
                "steps": {"s": {"type": "process.exec", "argv": ["python3", "-c", "import os\nprint(1)\n"]}},
            },
            _f,
        )

_script = os.path.join(HERE, "..", "play", "resources", "reach_analysis.py")
_r = subprocess.run([sys.executable, _script, "all", _store, "false"], capture_output=True, text=True, timeout=120)
check("large store: exits zero", _r.returncode == 0, _r.stderr[:200])
_ok = False
try:
    _out = json.loads(_r.stdout)
    _ok = True
except ValueError:
    _out = {}
check("large store: stdout is valid JSON", _ok, _r.stdout[:120])
if _ok:
    check("large store: count is the TOTAL read, not the trimmed list", _out.get("count", 0) >= 90, _out.get("count"))
    check(
        "large store: per-package detail is capped", len(_out.get("packages", [])) <= 60, len(_out.get("packages", []))
    )
    check(
        "large store: the trim is disclosed, not silent",
        _out.get("trimmed") == _out.get("count", 0) - _out.get("detailed", 0),
        (_out.get("count"), _out.get("detailed"), _out.get("trimmed")),
    )
    check("large store: stdout stays well under the step output limit", len(_r.stdout) < 120000, len(_r.stdout))

print()
print("%d checks, %d failed" % (CHECKS, len(FAILURES)))
if FAILURES:
    for f in FAILURES:
        print("  failed:", f)
    sys.exit(1)
print("ok")
