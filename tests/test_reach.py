#!/usr/bin/env python3
"""Hermetic tests for the reach extractor.

Every case runs against a fixture package in tests/fixtures, so this suite needs
nothing installed and no packages pulled. Run it with:

    python3 tests/test_reach.py

It is deliberately dependency free and runs on python 3.9 and newer, which is the
floor a stranger on a stock macOS actually has.
"""

import contextlib
import importlib.util
import json
import os
import shutil
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

print("unportable argv paths: a step that cannot run for anyone but its author")
_cases = [
    ("a path in a user home directory is reported", {"s": {"argv": ["node", "/Users/bob/proj/cli.js"]}}, True),
    (
        "argv[0] itself in a home directory is reported",
        {"s": {"argv": ["/home/vscode/.cache/venv/bin/python", "x.py"]}},
        True,
    ),
    ("a tilde path is reported", {"s": {"argv": ["python3", "~/secret/thing.py"]}}, True),
    (
        "a @resource reference travels in the archive and is not reported",
        {"s": {"argv": ["python3", "@resource{body.py}"]}},
        False,
    ),
    (
        "/usr/bin/env exists everywhere and is not reported",
        {"s": {"argv": ["/usr/bin/env", "python3", "-c", "x=1"]}},
        False,
    ),
    ("/bin/sh is not reported", {"s": {"argv": ["/bin/sh", "-c", "echo hi"]}}, False),
    ("a homebrew bin path is not reported", {"s": {"argv": ["/opt/homebrew/bin/jq", "."]}}, False),
    ("a relative path is not reported", {"s": {"argv": ["python3", "scripts/run.py"]}}, False),
    ("a bare command is not reported", {"s": {"argv": ["git", "status"]}}, False),
    ("a flag is not mistaken for a path", {"s": {"argv": ["ls", "-la"]}}, False),
]
for _label, _steps, _want in _cases:
    check(_label, (len(mod.portability_paths(_steps)) > 0) == _want, mod.portability_paths(_steps))


# ---------------------------------------------------------------------------
# Shell reading. These three cases came from running the tool over other people's
# published Plays, where a false positive is worse than a miss. Each has a negative
# control so the fix cannot be a blanket ban that silences real commands.
def shell_execs(body):
    r = mod.Reach()
    mod.scan_shell(body, r, "test")
    return set(r.execs)


print()
print("shell reading")

_c = shell_execs("# collect `docker-compose.prod.yml` and `compose.override.yml` too\nfind . -name x\n")
check(
    "a backtick inside a comment is not a command",
    "docker-compose.prod.yml" not in _c and "compose.override.yml" not in _c,
    sorted(_c),
)
check("the real command beside that comment is still read", "find" in _c, sorted(_c))

_c = shell_execs("x=`curl -s https://example.com`\n")
check("a backtick substitution outside a comment is still read", "curl" in _c, sorted(_c))

_c = shell_execs('find . -name x 2>/dev/null | LC_ALL=C sort -u >"$found"\n')
check("a redirected file descriptor is not a command", "2" not in _c, sorted(_c))
check("commands around that redirect are still read", "find" in _c and "sort" in _c, sorted(_c))

# The real-world shape: a multi-line find whose continuation line begins with the redirect.
_c = shell_execs('find . \\\n  -name x \\\n2>/dev/null | LC_ALL=C sort -u >"$found"\n')
check("a continuation line starting with a redirect is not a command", "2" not in _c, sorted(_c))
check("the pipeline on that continuation line is still read", "sort" in _c, sorted(_c))

_c = shell_execs("7z x archive.zip\n")
check("a real command starting with a digit is still read", "7z" in _c, sorted(_c))
_c = shell_execs("2to3 -w file.py\n")
check("2to3 is still read", "2to3" in _c, sorted(_c))

_c = shell_execs('case "$flag" in\n  true|1|yes|y) git status ;;\n  *) echo no ;;\nesac\n')
check("a case label alternation is not a list of commands", not ({"1", "yes", "y", "true"} & _c), sorted(_c))
check("the command inside a case branch is still read", "git" in _c, sorted(_c))

_c = shell_execs("(cd /tmp && git status)\n")
check("a subshell close paren is not a case label", "git" in _c, sorted(_c))

_c = shell_execs("arr=(a b c)\ngit status\n")
check("an array assignment does not swallow the next command", "git" in _c, sorted(_c))

_c = shell_execs("deploy() {\n  git push\n}\n")
check("a function definition does not swallow its body", "git" in _c, sorted(_c))


# Adversarial cases for the comment stripper and the case-label cut. Each one is a way
# the two could eat a real command, which is the failure mode that matters most here.
print()
print("shell reading, adversarial")

_hazards = [
    ("a # inside a parameter expansion is not a comment", "base=${FILE#*/}\ngit status\n", "git"),
    ("a ## inside a parameter expansion is not a comment", "base=${FILE##*/}\ngit status\n", "git"),
    ("a URL fragment is not a comment", "curl https://example.com/page#section\n", "curl"),
    ("an escaped hash is not a comment", "echo \\# ; git status\n", "git"),
    ("a hash inside single quotes is not a comment", "echo 'a # b'\ngit status\n", "git"),
    ("a hash inside double quotes is not a comment", 'echo "a # b"\ngit status\n', "git"),
    ("arithmetic parens do not cut the line", "y=$((1 + 2))\ngit status\n", "git"),
    ("process substitution does not cut the line", "diff <(sort a) <(sort b)\ngit status\n", "git"),
    ("a paren inside a quoted argument does not cut the line", 'grep "foo)" file\ngit status\n', "git"),
    ("a multi-line subshell does not lose its body", "(\n  cd /tmp\n  git status\n)\n", "git"),
    (
        "a case fallthrough does not lose the next branch",
        'case "$x" in\n  a) true ;;&\n  b) git status ;;\nesac\n',
        "git",
    ),
    ("a command after esac is still read", 'case "$x" in\n  a) true ;;\nesac\ngit status\n', "git"),
    ("a comment line before a real command does not eat it", "# note\ngit status\n", "git"),
    ("a trailing comment does not eat its own line", "git status  # check the tree\n", "git"),
]
for _label, _body, _want in _hazards:
    _c = shell_execs(_body)
    check(_label, _want in _c, sorted(_c))

# The stripper must not disturb an embedded python body.
_c = shell_execs("python3 -c 'import os\nprint(os.getcwd())'\n")
check("an embedded python body is still read as python", "python3" in _c, sorted(_c))


# A body in a language this tool does not read must be named. Silence about it is
# indistinguishable from finding nothing in it, and that is how a node -e body that
# requires fs looked like a Play that reaches nothing at all.
print()
print("bodies this tool cannot read")


def unread(steps):
    r = mod.Reach()
    for name, st in steps.items():
        mod.scan_step(st, FIX, r, name)
    return r.unanalysed


_node = {"s": {"type": "process.exec", "argv": ["node", "-e", "const fs=require('fs'); fs.readdirSync('.')"]}}
check("an inline node body is reported as unread", len(unread(_node)) == 1, unread(_node))

_deno = {"s": {"type": "process.exec", "argv": ["deno", "eval", "console.log(1)"]}}
check("an inline deno body is reported as unread", len(unread(_deno)) == 1, unread(_deno))

_ruby = {"s": {"type": "process.exec", "argv": ["ruby", "-e", "puts 1"]}}
check("an inline ruby body is reported as unread", len(unread(_ruby)) == 1, unread(_ruby))

# Negative controls: the languages it does read must still be read, not marked unread.
_py = {"s": {"type": "process.exec", "argv": ["python3", "-c", "import os\nos.getcwd()"]}}
check("an inline python body is still read, not marked unread", unread(_py) == [], unread(_py))

_sh = {"s": {"type": "process.exec", "argv": ["sh", "-c", "git status"]}}
check("an inline shell body is still read, not marked unread", unread(_sh) == [], unread(_sh))

_node_plain = {"s": {"type": "process.exec", "argv": ["node", "script.js"]}}
check("node without an eval flag reports nothing spurious", unread(_node_plain) == [], unread(_node_plain))

_node_empty = {"s": {"type": "process.exec", "argv": ["node", "-e", "   "]}}
check("an empty inline body reports nothing", unread(_node_empty) == [], unread(_node_empty))

_c = shell_execs("git status\n")
check("the shell reader still works after the change", "git" in _c, sorted(_c))


# A resources subdirectory this process cannot read was skipped in silence, and its modules
# then looked like missing dependencies. The finding stays, because it is genuinely unknown,
# but it must no longer arrive without a reason attached.
print()
print("an unreadable resources directory is disclosed")

_t = tempfile.mkdtemp()
try:
    os.makedirs(os.path.join(_t, "resources", "scripts"))
    with open(os.path.join(_t, "manifest.json"), "w") as fh:
        json.dump(
            {
                "name": "t",
                "description": "t",
                "metadata": {"runtime_dependencies": {"host_tools": []}},
                "steps": {"s": {"type": "process.exec", "argv": ["python3", "@resource{main.py}"]}},
            },
            fh,
        )
    with open(os.path.join(_t, "resources", "main.py"), "w") as fh:
        fh.write("import helper\n")
    with open(os.path.join(_t, "resources", "scripts", "helper.py"), "w") as fh:
        fh.write("X = 1\n")

    _ok = mod.analyze(_t, "t")
    check(
        "a sibling module in a readable subdirectory is not missing",
        _ok.get("missing_modules") == [],
        _ok.get("missing_modules"),
    )
    check("and nothing is said when there is nothing to say", not _ok.get("notes"), _ok.get("notes"))

    os.chmod(os.path.join(_t, "resources", "scripts"), 0o000)
    _bad = mod.analyze(_t, "t")
    check(
        "an unreadable resources directory is named in the notes",
        any("could not be read" in n for n in (_bad.get("notes") or [])),
        _bad.get("notes"),
    )
    check(
        "and it is recorded as a body that was not read",
        any("could not be read" in str(u.get("reason", "")) for u in (_bad.get("unanalysed_bodies") or [])),
        _bad.get("unanalysed_bodies"),
    )
finally:
    with contextlib.suppress(OSError):
        os.chmod(os.path.join(_t, "resources", "scripts"), 0o755)
    shutil.rmtree(_t, ignore_errors=True)

# ---------------------------------------------------------------------------
# A command that is reached but not reported is a false clean, which is the one
# failure this tool exists to prevent. These four shapes each hid a real command.
# Found after a competitor described the same two under-reports in their own tool;
# testing ours against the shapes they named turned up a third that was wider than
# either. Every positive has a negative control beside it, because the cheap way to
# pass an under-reporting test is to start reporting everything.
print()
print("commands that were reaching but not reported")

_c = shell_execs("""d=$(mktemp -d); rm -rf "$d"\n""")
check("a command after a substitution on the same line is read", "rm" in _c, sorted(_c))
check("and the substitution's own command is still read", "mktemp" in _c, sorted(_c))

_c = shell_execs("""v=hello; curl -s https://example.com\n""")
check("the same line without a substitution is unchanged", _c == {"curl"}, sorted(_c))

_c = shell_execs("""a=$(dirname $(readlink -f x)); curl y\n""")
check("a nested substitution reads the outer command too", "dirname" in _c, sorted(_c))
check("and the inner one", "readlink" in _c, sorted(_c))

_c = shell_execs("""n=$((1 + 2)); curl y\n""")
check("arithmetic expansion is not read as a command", _c == {"curl"}, sorted(_c))

_r = mod.Reach()
mod.scan_shell("""v=$(mktemp; curl y""", _r, "test")
check(
    "an unbalanced substitution says so rather than going quiet",
    any("unbalanced" in n for n in _r.notes),
    _r.notes,
)

_c = shell_execs("""l=$(mktemp); trap 'rm -f "$l"' EXIT\n""")
check("a command inside a trap handler is read", "rm" in _c, sorted(_c))
_c = shell_execs("""trap 'rm -rf "$workdir"' EXIT\n""")
check("including a destructive one, which is the point", _c == {"rm"}, sorted(_c))
_c = shell_execs("""trap - EXIT\n""")
check("and a trap that clears a handler invents nothing", _c == set(), sorted(_c))

_c = shell_execs("""find . -name '*.py' -exec grep -l TODO {} +\n""")
check("the command after find -exec is read", "grep" in _c, sorted(_c))
_c = shell_execs("""find . -type f -exec chmod 600 {} \\;\n""")
check("in the semicolon-terminated form as well", "chmod" in _c, sorted(_c))
_c = shell_execs("""find . -name '*.py' -print\n""")
check("and find without -exec adds nothing", _c == {"find"}, sorted(_c))

# A plain `( ... )` is left exactly as it was. Its commands already read correctly, because the
# paren is treated as a grammar word. Turning those parens into separators instead, which an
# earlier version of this fix did, invented `HEAD`, `branch`, `detached`, `parts` and `x.strip`
# out of case labels and embedded Python across 319 published packages.
_c = shell_execs("""(curl x && rm -rf y)\n""")
check("a plain subshell's commands are read, both of them", _c == {"curl", "rm"}, sorted(_c))
_c = shell_execs("""case "$v" in\n  HEAD|branch) printf p ;;\nesac\n""")
check("a case label is not read as a command", _c == set(), sorted(_c))


# ---------------------------------------------------------------------------
# `replace` is the one name in PY_PATH_METHODS that a builtin type also owns. Counting
# `str.replace` as a filesystem write told 17 of 319 published packages that they write to
# the working directory when they only rewrite a string, including two of the platform's own.
# Claiming a write that does not happen is worse than missing one, so the arity check refuses
# anything ambiguous. Found by auditing a Play whose author had deliberately declared no writes.
print()
print("writes that were being invented")


def py_writes(src):
    r = mod.Reach()
    mod.scan_python(src, r, "test")
    return r.writes


check(
    "str.replace with two arguments is not a write",
    py_writes('x = t.replace(" ", "_")') == [],
    py_writes('x = t.replace(" ", "_")'),
)
check("nor with three", py_writes('x = t.replace("a", "b", 1)') == [], py_writes('x = t.replace("a", "b", 1)'))
check(
    "nor chained",
    py_writes('x = t.replace(FS, "?").replace(RS, "?")') == [],
    py_writes('x = t.replace(FS, "?").replace(RS, "?")'),
)
_w = py_writes('from pathlib import Path\nPath("a.txt").replace("b.txt")')
check("but Path.replace with one argument still is", len(_w) == 1, _w)
_w = py_writes('from pathlib import Path\nPath("a").write_text("x")')
check("and write_text is untouched", len(_w) == 1, _w)
_w = py_writes('from pathlib import Path\nPath("a").unlink()')
check("and unlink is untouched", len(_w) == 1, _w)

# A write whose target could not be read was recorded with an unknown scope and then dropped,
# because only the cwd and outside_cwd buckets were reported. A package writing through a Path
# held in an attribute therefore read as writing nothing at all: the silence this tool reports.
# Across 416 installed packages this surfaced 99 previously invisible writes in 49 of them, and
# moved nothing that was already reported.
print()
print("writes through a Path that is not a literal")


def scopes(src):
    return sorted((w["scope"], w["target"]) for w in py_writes(src))


_attr = 'from pathlib import Path\n\n\nclass C:\n    def __init__(self, root: Path) -> None:\n        self.root = root\n        self.root.mkdir(parents=True, exist_ok=True)\n'
check("a Path held in an attribute is recorded, not dropped", scopes(_attr) == [("unknown", None)], scopes(_attr))
_var = 'from pathlib import Path\n\n\ndef f(target):\n    tmp = target.with_suffix(".tmp")\n    tmp.write_text("x")\n'
check("so is a Path held in a local variable", scopes(_var) == [("unknown", None)], scopes(_var))
_home = 'from pathlib import Path\n(Path.home() / ".cache" / "x").mkdir()'
check("a Path built from constants under home scopes outside the working directory", scopes(_home) == [("outside_cwd", "~/.cache/x")], scopes(_home))
_join = 'from pathlib import Path\nPath.home().joinpath(".cache", "x").mkdir()'
check("joinpath with constant parts resolves the same way", scopes(_join) == [("outside_cwd", "~/.cache/x")], scopes(_join))
_rel = 'from pathlib import Path\n(Path("out") / "r.txt").write_text("hi")'
check("and a relative one still scopes to the working directory", scopes(_rel) == [("cwd", "out/r.txt")], scopes(_rel))
_cwd = 'from pathlib import Path\n(Path.cwd() / "r.txt").write_text("hi")'
check("Path.cwd() is the working directory", scopes(_cwd) == [("cwd", "r.txt")], scopes(_cwd))
_mixed = 'from pathlib import Path\n(Path.home() / name / "x").write_text("hi")'
check("a non-constant segment leaves the whole target unresolved", scopes(_mixed) == [("unknown", None)], scopes(_mixed))
check(
    "the arity guard still holds for a variable receiver",
    py_writes('x = t.replace(" ", "_")\ny = u.replace("a", "b", 1)') == [],
    py_writes('x = t.replace(" ", "_")\ny = u.replace("a", "b", 1)'),
)


# ---------------------------------------------------------------------------
# This reads the copy installed on the machine, which is a snapshot from whenever it was pulled.
# An author who has since declared a tool still showed up as reaching it undeclared, so a fixed
# Play and a broken one looked identical. Reported by lgoyal6, whose license-guard had gone from
# 1 declared tool at 0.1.0 to 9 at 0.5.0 while our installed copy stayed at 0.1.0.
print()
print("the age of what was read")

_t = tempfile.mkdtemp()
try:
    os.makedirs(os.path.join(_t, "resources"))
    with open(os.path.join(_t, "manifest.json"), "w") as fh:
        fh.write(json.dumps({"name": "x", "steps": {}, "metadata": {}}))
    _v, _p = mod.installed_snapshot(_t)
    check("a package with no .rote-source reports no version rather than guessing", _v is None and _p is None, (_v, _p))
    with open(os.path.join(_t, ".rote-source"), "w") as fh:
        fh.write(json.dumps({"artifact_version": "0.1.0", "pulled_at": "2026-09-02T17:07:11Z"}))
    _v, _p = mod.installed_snapshot(_t)
    check("and reports both when the file is there", _v == "0.1.0" and _p.startswith("2026-09-02"), (_v, _p))
    with open(os.path.join(_t, ".rote-source"), "w") as fh:
        fh.write("{not json")
    _v, _p = mod.installed_snapshot(_t)
    check("a corrupt .rote-source degrades rather than raising", _v is None, (_v, _p))
    _r = mod.analyze(_t, "x")
    check("analyze carries the installed version through", "installed_version" in _r, sorted(_r.keys())[:6])
finally:
    shutil.rmtree(_t, ignore_errors=True)


print()
print("quoted-bodies: quoted text is data, and a builtin forks nothing")
# 2026-09-03: a competitor reproduced `kill -0 $$` reported as an undeclared executable (kill is
# missing from compgen -b's set in SHELL_BUILTINS), and five of Laksh's Plays reported `import`,
# `print` and `out.append`, because a `$(python3 -c '...')` span was cut at the first `)` inside
# the python program, and a backtick inside a single-quoted regex read every `|` alternative as a
# command.
r = analyze("quoted-bodies")
check(
    "the python3 -c body is read as python: its subprocess target is found",
    "quoted-body-tool" in r["reached"],
    r["reached"],
)
check("commands after the substitution are still read", {"grep", "python3"} <= set(r["reached"]), r["reached"])
check("printf is a builtin too, so it is not an executable reach", "printf" not in r["reached"], r["reached"])
check(
    "python words inside the quoted program are not commands",
    not ({"import", "print", "out.append", "paths", "path", "out", "for", "subprocess.run"} & set(r["reached"])),
    r["reached"],
)
check(
    "regex alternatives after a backtick inside single quotes are not commands",
    not ({"GET", "POST", "PUT", "get", "post", "route"} & set(r["reached"])),
    r["reached"],
)
check("kill is a shell builtin, never an undeclared executable", "kill" not in r["reached"], r["reached"])
check("no line was skipped as untokenisable, so nothing was read as shell by accident", r["notes"] == [], r["notes"])
check("kill is in the builtin set that compgen -b names", "kill" in mod.SHELL_BUILTINS)

print()
print("duplicate copies: an authoring copy at the store root beside its pulled copy counts once")
_dup = tempfile.mkdtemp()
try:
    shutil.copytree(os.path.join(FIX, "shell-nested"), os.path.join(_dup, "shell-nested"))
    shutil.copytree(os.path.join(FIX, "shell-nested"), os.path.join(_dup, "someone", "shell-nested"))
    _one = mod.analyze(os.path.join(FIX, "shell-nested"), "someone/shell-nested")
    _out = subprocess.run(
        [sys.executable, EXTRACTOR, "all", _dup, "false"], capture_output=True, text=True, timeout=120
    )
    _p = json.loads(_out.stdout)
    check("both copies are read", _p["count"] == 2, _p["count"])
    check("the root copy is named as a duplicate", _p["duplicates"] == ["shell-nested"], _p["duplicates"])
    check(
        "the headline counts the reach once",
        _p["undeclared_total"] == len(_one["undeclared_but_reached"]) and _p["undeclared_total"] > 0,
        (_p["undeclared_total"], _one["undeclared_but_reached"]),
    )
    check("and the payload says so", "duplicate a pulled copy" in _p.get("warning", ""), _p.get("warning"))
    _rows = {q["ref"]: q for q in _p["packages"]}
    check(
        "the duplicate row still carries its reach, marked",
        _rows["shell-nested"].get("duplicate_of") == "someone/shell-nested",
        _rows["shell-nested"].get("duplicate_of"),
    )
finally:
    shutil.rmtree(_dup, ignore_errors=True)

print()
print("%d checks, %d failed" % (CHECKS, len(FAILURES)))
if FAILURES:
    for f in FAILURES:
        print("  failed:", f)
    sys.exit(1)
print("ok")
