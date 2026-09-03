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


print()
print("%d checks, %d failed" % (CHECKS, len(FAILURES)))
if FAILURES:
    for f in FAILURES:
        print("  failed:", f)
    sys.exit(1)
print("ok")
