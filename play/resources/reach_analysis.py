import ast
import importlib.util
import json
import os
import re
import shlex
import shutil
import sys


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"')
    return v


def _inline_list(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p) for p in inner.split(",")]
    return None


def frontmatter_steps(text):
    """Read the steps: block from a @rote-frontmatter JSDoc header.

    Understands only the shapes rote emits: scalar keys, block sequences under a key,
    and inline lists. Anything deeper is recorded as a note instead of being guessed at.
    """
    notes = []
    if "@rote-frontmatter" not in text:
        return {}, {}, ["no @rote-frontmatter block in main.ts"]
    body = text.split("@rote-frontmatter", 1)[1]
    lines, started = [], False
    for raw in body.splitlines():
        line = re.sub(r"^\s*\* ?", "", raw)
        if line.strip() == "---":
            if not started:
                started = True
                continue
            break
        if started:
            lines.append(line.rstrip())
    if not started:
        return {}, {}, ["frontmatter delimiters not found"]

    steps, meta = {}, {}
    i = 0
    while i < len(lines) and not lines[i].startswith("steps:"):
        if lines[i].startswith("metadata:"):
            i += 1
            while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
                k, _, v = lines[i].strip().partition(":")
                meta[k.strip()] = _scalar(v)
                i += 1
            continue
        i += 1
    if i >= len(lines):
        return steps, meta, [*notes, "no steps: block"]
    i += 1

    cur = None
    pend_key, pend_indent = None, -1
    while i < len(lines):
        l = lines[i]
        if not l.strip():
            i += 1
            continue
        indent = len(l) - len(l.lstrip(" "))
        if indent == 0:
            break
        s = l.strip()

        if pend_key is not None and indent < pend_indent:
            pend_key, pend_indent = None, -1

        if s.startswith("-") and (s == "-" or s.startswith("- ")):
            item = s[2:] if s.startswith("- ") else ""
            block = re.match(r"^([|>])(\d*)([-+]?)$", item.strip())
            if block:
                # The explicit indicator (the 2 in |2) is deliberately not used: the minimum
                # indent of the block reproduces manifest.json byte for byte across every
                # package tested, and the indicator does not when a body is indented further.
                buf, j = [], i + 1
                while j < len(lines):
                    nxt = lines[j]
                    if nxt.strip() == "":
                        buf.append("")
                        j += 1
                        continue
                    nind = len(nxt) - len(nxt.lstrip(" "))
                    if nind <= indent:
                        break
                    buf.append(nxt)
                    j += 1
                while buf and buf[-1] == "":
                    buf.pop()
                real = [b for b in buf if b.strip()]
                base = min((len(b) - len(b.lstrip(" "))) for b in real) if real else 0
                text = "\n".join(b[base:] if len(b) >= base else b.lstrip(" ") for b in buf)
                chomp = block.group(3)
                if chomp == "-":
                    text = text.rstrip("\n")
                elif chomp == "+":
                    text = text + "\n"
                else:
                    text = text.rstrip("\n") + "\n"
                value = text
                i = j
            else:
                value = _scalar(item)
                i += 1
            if pend_key == "argv" and cur is not None:
                steps[cur].setdefault("argv", []).append(value)
            elif pend_key and cur is not None:
                steps[cur].setdefault(pend_key, [])
                if isinstance(steps[cur][pend_key], list):
                    steps[cur][pend_key].append(value)
            continue

        if indent == 2 and s.endswith(":") and ": " not in s:
            cur = s[:-1].strip()
            steps[cur] = {}
            pend_key, pend_indent = None, -1
            i += 1
            continue

        if cur is None:
            i += 1
            continue

        k, sep, v = s.partition(":")
        if not sep:
            i += 1
            continue
        k, v = k.strip(), v.strip()
        if v == "":
            pend_key, pend_indent = k, indent
            if k not in ("argv", "depends_on"):
                notes.append("step %s: nested block %r summarised only" % (cur, k))
        else:
            il = _inline_list(v)
            steps[cur][k] = il if il is not None else _scalar(v)
            pend_key, pend_indent = None, -1
        i += 1
    return steps, meta, notes


def read_deps_toml(path):
    """Minimal reader for the [[tools]] blocks rote writes. tomllib is 3.11+, the floor is 3.9."""
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return out
    cur, in_tools = None, False
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[["):
            if s == "[[tools]]":
                if cur and cur.get("command"):
                    out[cur["command"]] = cur.get("required", False)
                cur, in_tools = {}, True
            else:
                if cur and cur.get("command"):
                    out[cur["command"]] = cur.get("required", False)
                cur, in_tools = None, False
            continue
        if s.startswith("["):
            if cur and cur.get("command"):
                out[cur["command"]] = cur.get("required", False)
            cur, in_tools = None, False
            continue
        if in_tools and cur is not None and "=" in s:
            k, _, v = s.partition("=")
            k, v = k.strip(), v.strip()
            if v.lower() in ("true", "false"):
                cur[k] = v.lower() == "true"
            else:
                cur[k] = v.strip("\"'")
    # fall through
    if cur and (cur.get("command") or cur.get("id")):
        out[cur.get("command") or cur["id"]] = cur.get("required", False)
    return {c: bool(r) for c, r in out.items()}


def load_package(pkg_dir):
    """Return (steps, metadata, declared, source, notes).

    A published or pulled package carries manifest.json. A package you are still writing
    carries only main.ts and deps.toml, which is the case this tool exists to cover.
    """
    mf = os.path.join(pkg_dir, "manifest.json")
    if os.path.isfile(mf):
        with open(mf, encoding="utf-8") as fh:
            m = json.load(fh)
        notes = []
        if not isinstance(m, dict):
            return {}, {}, {}, "manifest.json", ["manifest.json is not an object, so it cannot be read"]
        md = m.get("metadata") or {}
        if not isinstance(md, dict):
            notes.append("manifest metadata is not an object; declarations were skipped")
            md = {}
        rd = md.get("runtime_dependencies") or {}
        if not isinstance(rd, dict):
            rd = {}
        declared = {}
        tools = rd.get("host_tools") or []
        if not isinstance(tools, list):
            notes.append("host_tools is not a list; declared tools were skipped")
            tools = []
        for t in tools:
            if not isinstance(t, dict):
                notes.append("a host_tools entry is not an object and was skipped")
                continue
            cmd = t.get("command") or t.get("id")
            if cmd:
                declared[cmd] = bool(t.get("required"))
        steps = m.get("steps") or {}
        if not isinstance(steps, dict):
            notes.append("manifest steps is not an object, so no step could be read")
            steps = {}
        elif not steps:
            notes.append("this package declares no steps, so there is nothing to read")
        return steps, md, declared, "manifest.json", notes
    mt = os.path.join(pkg_dir, "main.ts")
    if os.path.isfile(mt):
        with open(mt, encoding="utf-8", errors="replace") as fh:
            steps, meta, notes = frontmatter_steps(fh.read())
        deps_path = os.path.join(pkg_dir, "deps.toml")
        declared = read_deps_toml(deps_path)
        if not os.path.isfile(deps_path):
            notes.append("no deps.toml in this package, so nothing is declared to compare against")
            source = "main.ts frontmatter, no deps.toml"
        else:
            source = "main.ts frontmatter and deps.toml"
            if not declared:
                notes.append("deps.toml declares no tools, or could not be read")
        if not steps:
            notes.append("this package declares no steps, so there is nothing to read")
        return steps, meta, declared, source, notes
    return {}, {}, {}, "nothing readable", ["no manifest.json and no main.ts in this directory"]


RESOURCE_RE = re.compile(r"@resource\{([^}]+)\}")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}

# Shell grammar words and shell builtins are not reached executables. A Play that runs `echo`
# is not depending on /bin/echo, and reporting `fi` or `then` as a dependency would be a false
# claim about somebody else's package, which is the worst failure this tool can have.
SHELL_KEYWORDS = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "for",
    "while",
    "until",
    "do",
    "done",
    "case",
    "esac",
    "in",
    "function",
    "select",
    "time",
    "coproc",
    "{",
    "}",
    "(",
    ")",
    "[[",
    "]]",
    "!",
    ";;",
    "&",
}
SHELL_BUILTINS = {
    "alias",
    "bg",
    "bind",
    "break",
    "builtin",
    "caller",
    "cd",
    "command",
    "compgen",
    "complete",
    "compopt",
    "continue",
    "declare",
    "dirs",
    "disown",
    "echo",
    "enable",
    "eval",
    "exec",
    "exit",
    "export",
    "false",
    "fc",
    "fg",
    "getopts",
    "hash",
    "help",
    "history",
    "jobs",
    "let",
    "local",
    "logout",
    "mapfile",
    "popd",
    "printf",
    "pushd",
    "pwd",
    "read",
    "readarray",
    "readonly",
    "return",
    "set",
    "shift",
    "shopt",
    "source",
    "suspend",
    "test",
    "times",
    "trap",
    "true",
    "type",
    "typeset",
    "ulimit",
    "umask",
    "unalias",
    "unset",
    "wait",
    ":",
    ".",
    "[",
}
_CMD_OK = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")
# These run another program. Reporting only the wrapper hides what actually runs.
FORWARDING = {
    "command",
    "exec",
    "env",
    "nohup",
    "time",
    "xargs",
    "sudo",
    "doas",
    "nice",
    "stdbuf",
    "timeout",
    "setsid",
    "ionice",
}
# `find . -exec grep -l TODO {} +` runs grep. A tool that reports only `find` has produced a
# false clean, which is the one failure this whole program exists to avoid.
FIND_EXEC = {"find"}
_FIND_EXEC_FLAGS = {"-exec", "-execdir", "-ok", "-okdir"}

_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def plausible_command(tok):
    """True only for tokens that can really be a command name.

    Deliberately conservative. An assignment, a flag, a glob, a variable, a redirection or a
    grammar word is not a command, and when in doubt this returns False so the tool stays quiet
    rather than inventing a dependency.
    """
    if not tok or tok in SHELL_KEYWORDS or tok in SHELL_BUILTINS:
        return False
    if "=" in tok or tok.startswith("-") or tok.startswith("$"):
        return False
    # `2>/dev/null` lexes to a bare `2`, and a line that begins with a redirect then puts
    # that digit in the command position. A real command can start with a digit (7z, 2to3)
    # but is never only digits.
    if tok.isdigit():
        return False
    if any(ch in tok for ch in "*?[]{}()<>|&;`\"'" + chr(92)):
        return False
    base = os.path.basename(tok)
    if base in SHELL_BUILTINS or base in SHELL_KEYWORDS or base.isdigit():
        return False
    return bool(_CMD_OK.match(base))


PY_INTERP = re.compile(r"^python3(\.\d+)?$|^python$")
PROC_CALLS = {"run", "call", "check_call", "check_output", "Popen"}
WRITE_CMDS = {"mkdir", "tee", "cp", "mv", "rm", "touch", "ln", "dd", "truncate", "install", "rsync"}
SHIM = "_rote_python_c.py"


def stdlib_names():
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return set(names)
    import sysconfig

    out = set(sys.builtin_module_names)
    try:
        d = sysconfig.get_paths()["stdlib"]
        for e in os.listdir(d):
            if e.endswith(".py"):
                out.add(e[:-3])
            elif os.path.isdir(os.path.join(d, e)) and os.path.exists(os.path.join(d, e, "__init__.py")):
                out.add(e)
    except OSError:
        pass
    return out


STDLIB = stdlib_names()


class Reach:
    def __init__(self):
        self.execs = set()
        self.dynamic = set()
        self.imports = set()
        self.envs = set()
        self.writes = []  # (command, target, scope)
        self.unanalysed = []  # bodies this tool cannot read, named rather than skipped
        self.notes = []


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_consts(tree):
    """Return (assigned, iterated).

    `assigned` maps a name to the ORDERED values of a literal assigned to it, so
    argv = ["ps", "-axo", ...] resolves to the command "ps" and not to its arguments.
    `iterated` maps a loop target to every value it takes, so `for h in TOOLS` reaches all
    of TOOLS. Conflating the two reports a command's own flags as separate dependencies.
    """
    assigned, iterated = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            v = node.value
            vals = None
            single = _const_str(v)
            if single is not None:
                vals = [single]
            elif isinstance(v, (ast.List, ast.Tuple)):
                items = [_const_str(e) for e in v.elts]
                if items and all(i is not None for i in items):
                    vals = items
            if vals:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        assigned[t.id] = vals
        elif isinstance(node, ast.For):
            it = node.iter
            values = None
            if isinstance(it, (ast.List, ast.Tuple, ast.Set)):
                cand = [_const_str(e) for e in it.elts]
                if cand and all(c is not None for c in cand):
                    values = cand
            elif isinstance(it, ast.Name):
                values = assigned.get(it.id)
            if values and isinstance(node.target, ast.Name):
                iterated.setdefault(node.target.id, set()).update(values)
    return assigned, iterated


def _resolve_argv0(node, assigned, iterated):
    """Return (candidate command names, unresolved) for the executable position of a call."""
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        node = node.elts[0]
    s = _const_str(node)
    if s is not None:
        return {s}, False
    if isinstance(node, ast.Name):
        if node.id in iterated:
            return set(iterated[node.id]), False
        vals = assigned.get(node.id)
        if vals:
            return {vals[0]}, False  # a bound argv list: the command is its head
        return set(), True
    return set(), True


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _dedupe_writes(writes, scope):
    """The whole-source and per-line shell passes can both see the same write. Report it once."""
    out, seen = [], set()
    for w in writes:
        if w["scope"] != scope:
            continue
        key = (w["command"], w["target"])
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


_NULL_SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero", "/dev/tty"}


def _path_scope(path):
    """Classify a write target as inside or outside the working directory.

    Normalises first, so out/../../etc/passwd is recognised as escaping rather than trusted
    because it happens to start with a relative segment.
    """
    if not isinstance(path, str) or not path:
        return "unknown", None
    if path in _NULL_SINKS:
        return "sink", path  # a device sink, not a filesystem write worth reporting
    if path.startswith("~") or os.path.isabs(path):
        return "outside_cwd", path
    norm = os.path.normpath(path)
    if norm == ".." or norm.startswith(".." + os.sep):
        return "outside_cwd", path
    return "cwd", path


# Python calls that write, remove or move something on disk. The writes column is a headline
# claim of this tool, and almost every Play has a Python body, so missing these made it report
# zero writes for packages that clearly write.
PY_WRITE_CALLS = {
    "os.makedirs": 1,
    "os.mkdir": 1,
    "os.remove": 1,
    "os.unlink": 1,
    "os.rmdir": 1,
    "os.rename": 2,
    "os.replace": 2,
    "os.truncate": 1,
    "os.symlink": 2,
    "os.link": 2,
    "shutil.copy": 2,
    "shutil.copy2": 2,
    "shutil.copyfile": 2,
    "shutil.copytree": 2,
    "shutil.move": 2,
    "shutil.rmtree": 1,
    "shutil.make_archive": 1,
}
PY_PATH_METHODS = {"write_text", "write_bytes", "mkdir", "touch", "unlink", "rmdir", "rename", "replace"}
# `replace` is the one name in that set that a builtin type also owns. `str.replace` and
# `bytes.replace` are everywhere, and counting them made 17 of 319 published packages look like
# they write to the working directory when they only rewrite a string. Argument count separates
# them cleanly: Path.replace takes exactly one target, str.replace takes two or three. Claiming a
# write that does not happen is worse than missing one, so anything ambiguous is not a write.
_ARITY_ONLY_PATH_METHOD = {"replace": 1}


def _record_py_write(reach, command, path_node):
    path = _const_str(path_node)
    scope, target = _path_scope(path)
    reach.writes.append({"command": command, "target": target, "scope": scope})


def _alias_map(tree):
    """Map local aliases back to their real module, so `import subprocess as sp` still matches."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = node.module + "." + a.name
    return aliases


def _normalise(name, aliases):
    if not name:
        return name
    head, _, rest = name.partition(".")
    if head in aliases:
        return aliases[head] + ("." + rest if rest else "")
    return name


def scan_python(src, reach, origin, depth=0):
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        reach.notes.append("unparseable python in %s: %s" % (origin, str(exc)[:60]))
        return
    assigned, iterated = _collect_consts(tree)
    aliases = _alias_map(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                reach.imports.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                reach.imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            raw = _dotted(node.func)
            name = _normalise(raw, aliases)
            tail = name.rsplit(".", 1)[-1]
            shell_kw = any(
                k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords
            )
            if name in ("os.system", "os.popen"):
                if node.args:
                    text = _const_str(node.args[0])
                    if text:
                        scan_shell(text, reach, origin, depth + 1)
                    else:
                        reach.dynamic.add("%s(<computed>)" % name)
            elif tail in PROC_CALLS and ("subprocess" in name or tail == "Popen"):
                if node.args:
                    if shell_kw:
                        # shell=True means the first argument is a command line, not an argv list.
                        text = _const_str(node.args[0])
                        if text:
                            scan_shell(text, reach, origin + ">shell=True", depth + 1)
                        else:
                            reach.dynamic.add("%s(shell=True, <computed>)" % name)
                    else:
                        got, dyn = _resolve_argv0(node.args[0], assigned, iterated)
                        reach.execs |= {c for c in got if plausible_command(c)}
                        if dyn:
                            reach.dynamic.add("%s(<computed>)" % name)
                        # subprocess.run(["sh", "-c", body]) is a shell body, and is as common
                        # as os.system, which already recurses.
                        arg0 = node.args[0]
                        if isinstance(arg0, (ast.List, ast.Tuple)) and len(arg0.elts) >= 3:
                            lead = _const_str(arg0.elts[0]) or ""
                            flag = _const_str(arg0.elts[1])
                            inner = _const_str(arg0.elts[2])
                            if os.path.basename(lead) in SHELLS and flag == "-c" and inner:
                                scan_shell(inner, reach, origin + ">argv sh -c", depth + 1)
                            elif PY_INTERP.match(os.path.basename(lead)) and flag == "-c" and inner:
                                scan_python(inner, reach, origin + ">argv python -c", depth + 1)
            elif name == "shutil.which" and node.args:
                got, dyn = _resolve_argv0(node.args[0], assigned, iterated)
                reach.execs |= {c for c in got if plausible_command(c)}
                if dyn:
                    reach.dynamic.add("shutil.which(<computed>)")
            elif name in ("os.getenv", "os.environ.get") and node.args:
                key = _const_str(node.args[0])
                if key:
                    reach.envs.add(key)
            elif name in PY_WRITE_CALLS:
                idx = PY_WRITE_CALLS[name] - 1
                target = node.args[idx] if len(node.args) > idx else None
                _record_py_write(reach, name, target)
            elif name == "open" or tail == "open":
                mode = None
                if len(node.args) > 1:
                    mode = _const_str(node.args[1])
                for k in node.keywords:
                    if k.arg == "mode":
                        mode = _const_str(k.value)
                if mode and any(ch in mode for ch in "wax+"):
                    _record_py_write(reach, "open(%s)" % mode, node.args[0] if node.args else None)
            elif tail in PY_PATH_METHODS and isinstance(node.func, ast.Attribute):
                # pathlib.Path("x").write_text(...) and friends
                want = _ARITY_ONLY_PATH_METHOD.get(tail)
                if want is not None and (len(node.args) != want or node.keywords):
                    continue  # str.replace(old, new), not Path.replace(target)
                recv = node.func.value
                inner = None
                if isinstance(recv, ast.Call) and recv.args:
                    inner = recv.args[0]
                _record_py_write(reach, "Path.%s" % tail, inner)
        elif isinstance(node, ast.Subscript) and _normalise(_dotted(node.value), aliases) == "os.environ":
            key = _const_str(node.slice)
            if key:
                reach.envs.add(key)


def _classify_write(head, tokens, reach):
    seen = []
    for t in tokens[1:]:
        if t.startswith("-"):
            continue
        cand = t
        if "=" in t:
            key, _, val = t.partition("=")
            if key.isalpha() and val:
                cand = val  # dd of=/etc/passwd
            else:
                continue
        if cand not in seen:
            seen.append(cand)
    if not seen:
        reach.writes.append({"command": head, "target": None, "scope": "unknown"})
        return
    # Every target, not only the last: recording one reported `rm -rf /etc/thing out` as a write
    # inside the working directory, which is the opposite of the truth.
    for tgt in seen:
        scope, target = _path_scope(tgt)
        reach.writes.append({"command": head, "target": target, "scope": scope})


_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def _unescaped_quotes(line):
    """Quote characters in a line, ignoring escaped ones, to track a string spanning lines."""
    out, i = [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch in ("'", '"'):
            out.append(ch)
        i += 1
    return out


def _strip_comments(src):
    """Remove shell comments, quote aware.

    The per-line pass already skips comments, but the substitution pass reads the whole
    source at once, so a backtick used as prose punctuation inside a comment was being read
    as a command substitution and reported as a dependency.
    """
    out = []
    quote = None
    for raw in src.splitlines():
        buf = []
        i = 0
        prev_ws = True
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and quote != "'":
                buf.append(ch)
                if i + 1 < len(raw):
                    buf.append(raw[i + 1])
                i += 2
                prev_ws = False
                continue
            if quote is not None:
                buf.append(ch)
                if ch == quote:
                    quote = None
                i += 1
                prev_ws = False
                continue
            if ch in ("'", '"'):
                quote = ch
                buf.append(ch)
                i += 1
                prev_ws = False
                continue
            if ch == "#" and prev_ws:
                break
            buf.append(ch)
            prev_ws = ch.isspace()
            i += 1
        out.append("".join(buf))
    return "\n".join(out)


def _outer_substitutions(src, truncated):
    """Yield the body of each outermost `$( ... )` and each backtick span.

    A regex cannot do this. `[^()]*` cannot cross a nested paren, so in
    `$(dirname $(readlink -f x))` it matched only the inner span and `dirname` was never read,
    which is a silent miss in the one direction this tool must not have. Only the OUTERMOST span
    is yielded because scanning it recurses back through here and finds the inner ones.
    `$((...))` is arithmetic, not a command, and is skipped.
    """
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "`":
            j = src.find("`", i + 1)
            if j < 0:
                truncated[0] = True
                return
            yield src[i + 1 : j]
            i = j + 1
            continue
        if ch == "$" and i + 1 < n and src[i + 1] == "(":
            if i + 2 < n and src[i + 2] == "(":
                i += 3  # arithmetic expansion
                continue
            depth, j = 1, i + 2
            while j < n and depth:
                if src[j] == "(":
                    depth += 1
                elif src[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                truncated[0] = True  # unbalanced, refuse to guess, but say so
                return
            yield src[i + 2 : j - 1]
            i = j
            continue
        i += 1


def _scan_substitutions(src, reach, origin, depth):
    """A command substitution runs a command. Read what is inside it."""
    if depth > 4:
        return
    truncated = [False]
    for inner in _outer_substitutions(src, truncated):
        if inner and inner.strip():
            scan_shell(inner, reach, origin + ">substitution", depth + 1)
    if truncated[0]:
        reach.notes.append(
            "an unbalanced command substitution in %s stopped the read partway, so the "
            "commands after it were not examined" % origin
        )


def _tokens(text):
    """Tokenise one chunk of shell. Returns None when the chunk cannot be lexed."""
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return None


_SEPS = {";", "|", "||", "&&", "&", ";;"}


def _case_label_cut(toks):
    """Index of a close paren that has no opener, or -1.

    `true|1|yes|y) cmd ;;` splits on the pipes into what looks like four commands. In valid
    shell an unmatched close paren terminates a case label, so everything up to it is data.
    """
    depth = 0
    for i, t in enumerate(toks):
        if not t or set(t) - {"(", ")"}:
            continue
        for ch in t:
            if ch == "(":
                depth += 1
            else:
                if depth == 0:
                    return i
                depth -= 1
    return -1


def _elide_substitutions(toks):
    """Remove `$( ... )` spans and turn leftover punctuation back into separators.

    shlex with punctuation_chars lexes `d=$(mktemp -d); rm -rf "$d"` as
    ['d=$', '(', 'mktemp', '-d', ');', 'rm', ...]. `);` is not in _SEPS, so the line stayed one
    segment and the scanner abandoned it at the paren: every command after a substitution on the
    same line went unreported, which is a false clean on an ordinary shell idiom. The body of the
    substitution is already read by _scan_substitutions, so the span is dropped here rather than
    read twice.

    Only `$( ... )` is touched. A plain `( ... )` is left byte for byte as it was, because turning
    those parens into separators made the scanner read case-branch labels and Python expressions
    inside embedded source as commands: across 319 published packages it invented `HEAD`,
    `branch`, `detached`, `parts` and `x.strip`. A subshell's contents therefore still go unread,
    which is a miss rather than an invention, and a false positive is the worse of the two.
    """
    out, sub_depth = [], 0
    for t in toks:
        if not t:
            continue
        if set(t) <= {"(", ")", ";"}:
            keep = ""
            for ch in t:
                if ch == "(":
                    if sub_depth or (out and out[-1].endswith("$")):
                        sub_depth += 1
                    else:
                        keep += ch  # a plain paren, left exactly as it was
                elif ch == ")":
                    if sub_depth:
                        sub_depth -= 1
                    else:
                        keep += ch
                elif not sub_depth:
                    if keep:
                        out.append(keep)
                        keep = ""
                    out.append(";")
            if keep:
                out.append(keep)
            continue
        if sub_depth:
            continue
        out.append(t)
    return out


def _scan_tokens(toks, reach, origin, depth):
    cut = _case_label_cut(toks)
    if cut >= 0:
        toks = toks[cut + 1 :]
    toks = _elide_substitutions(toks)
    segs, cur = [], []
    for t in toks:
        if t in _SEPS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    for seg in segs:
        if not seg:
            continue
        for i, tok in enumerate(seg):
            if tok in (">", ">>"):
                tgt = seg[i + 1] if i + 1 < len(seg) else None
                scope, target = _path_scope(tgt)
                reach.writes.append({"command": "redirect", "target": target, "scope": scope})
        if seg[0] in ("for", "select", "case"):
            continue  # the loop variable and its word list are data, not commands
        while len(seg) > 1 and seg[1] == ")":
            seg = seg[2:]  # a case branch label, not a command
        if not seg:
            continue
        idx = 0
        while idx < len(seg) and (seg[idx] in SHELL_KEYWORDS or _ASSIGN_RE.match(seg[idx])):
            idx += 1
        if idx >= len(seg):
            continue
        head = seg[idx]
        seg = seg[idx:]
        # Step through wrappers such as `command curl ...` or `env FOO=1 curl ...` so the
        # program that actually runs is the one reported.
        hops = 0
        while seg and os.path.basename(seg[0]) in FORWARDING and len(seg) > 1 and hops < 4:
            wrapper = os.path.basename(seg[0])
            if plausible_command(wrapper):
                reach.execs.add(wrapper)  # env and sudo are real binaries; command and exec are not
            seg = seg[1:]
            while seg and (seg[0].startswith("-") or _ASSIGN_RE.match(seg[0])):
                seg = seg[1:]
            head = seg[0] if seg else ""
            hops += 1
        # `trap 'rm -f "$tmp"' EXIT` carries a real command inside a quoted body. trap is a
        # shell builtin, so the filter below drops the whole segment, and a cleanup handler is
        # exactly where a destructive command hides.
        if os.path.basename(head) == "trap" and len(seg) > 1 and depth <= 4:
            scan_shell(seg[1], reach, origin + ">trap", depth + 1)
        if not seg or not plausible_command(head):
            continue
        base = os.path.basename(head)
        reach.execs.add(base)
        if base in FIND_EXEC and depth <= 4:
            for _i, _t in enumerate(seg):
                if _t not in _FIND_EXEC_FLAGS:
                    continue
                sub = []
                for u in seg[_i + 1 :]:
                    if u == "+":
                        break
                    sub.append(u)
                if sub:
                    _scan_tokens(sub, reach, origin + ">" + base + " " + _t, depth + 1)
        if base in WRITE_CMDS:
            _classify_write(base, seg, reach)
        if PY_INTERP.match(base) and "-c" in seg:
            i = seg.index("-c")
            if i + 1 < len(seg):
                scan_python(seg[i + 1], reach, origin + ">python -c", depth + 1)
        elif base in SHELLS and "-c" in seg:
            i = seg.index("-c")
            if i + 1 < len(seg):
                scan_shell(seg[i + 1], reach, origin + ">sh -c", depth + 1)


def scan_shell(src, reach, origin, depth=0):
    """Read a shell body two ways and union the result.

    The whole-source pass keeps a multi-line quoted body, such as a nested python3 -c, intact.
    The per-line pass exists because shlex treats a newline as ordinary whitespace, so without it
    a real script collapses into one segment and every command after the first is lost. Both
    passes feed the same conservative command filter, so the union cannot invent a dependency.
    """
    if depth > 4:
        reach.notes.append("shell nesting too deep in %s" % origin)
        return
    # A single-line body is read whole. A multi-line body is read line by line, because shlex
    # treats a newline as ordinary whitespace: reading it whole merges every line into one
    # segment, which loses every command after the first and makes the first command appear to
    # write to every path in the script.
    _scan_substitutions(_strip_comments(src), reach, origin, depth)
    if "\n" not in src:
        whole = _tokens(src)
        if whole is not None:
            _scan_tokens(whole, reach, origin, depth)
        else:
            reach.notes.append("unlexable shell in %s" % origin)
        return
    whole = None
    if True:
        unlexable = 0
        heredoc = None
        open_quote = None
        collecting = None
        for raw_line in src.splitlines():
            line = raw_line.strip()
            # Heredoc text is data for another program, not shell commands.
            if heredoc is not None:
                if line == heredoc:
                    heredoc = None
                continue
            # An unterminated quote means the following lines are an embedded program, usually a
            # python3 -c body. Reading those as shell invented commands such as `import`.
            if open_quote is not None:
                if collecting is not None:
                    end = raw_line.find(open_quote)
                    collecting["lines"].append(raw_line if end < 0 else raw_line[:end])
                if open_quote in _unescaped_quotes(raw_line):
                    open_quote = None
                    if collecting is not None:
                        body = "\n".join(collecting["lines"])
                        cmd = os.path.basename(collecting["cmd"] or "")
                        if PY_INTERP.match(cmd):
                            scan_python(body, reach, origin + ">embedded python", depth + 1)
                        elif cmd in SHELLS:
                            scan_shell(body, reach, origin + ">embedded sh", depth + 1)
                        collecting = None
                continue
            hd = _HEREDOC_RE.search(line)
            if hd:
                heredoc = hd.group(1)
            quotes = _unescaped_quotes(line)
            opener = None
            for q in quotes:
                if open_quote == q:
                    open_quote = None
                elif open_quote is None:
                    open_quote = q
                    opener = q
            if not line or line.startswith("#"):
                continue
            if open_quote is not None and opener is not None:
                # This line starts an embedded body, for example python3 -c 'first line.
                # Read the command before the quote, then collect the body and read it in its
                # own language rather than as more shell.
                cut = line.find(opener)
                prefix = line[:cut]
                toks = _tokens(prefix)
                if toks:
                    _scan_tokens(toks, reach, origin, depth)
                pending_cmd = [t for t in (toks or []) if plausible_command(t)]
                embedded = {"quote": opener, "cmd": pending_cmd[0] if pending_cmd else "", "lines": [line[cut + 1 :]]}
                collecting = embedded
                continue
            toks = _tokens(line)
            if toks is None:
                unlexable += 1
                continue
            _scan_tokens(toks, reach, origin, depth)
        if unlexable:
            reach.notes.append("%s: %d line(s) could not be tokenised and were skipped" % (origin, unlexable))
    elif whole is None:
        reach.notes.append("unlexable shell in %s" % origin)


def read_resource(pkg_dir, name):
    """Read resources/<name>, refusing anything that escapes the package directory.

    A package is untrusted input, so a reference like @resource{../../etc/passwd} must not
    be followed. Returns (text, problem); exactly one of the two is None.
    """
    base = os.path.realpath(os.path.join(pkg_dir, "resources"))
    target = os.path.realpath(os.path.join(base, name))
    if target != base and not target.startswith(base + os.sep):
        return None, "refused: the reference escapes the package directory"
    if not os.path.isfile(target):
        return None, "missing or unreadable in the package"
    try:
        with open(target, encoding="utf-8", errors="replace") as fh:
            return fh.read(), None
    except OSError as exc:
        return None, "unreadable: %s" % str(exc)[:50]


_PY_EXT = (".py",)
_SH_EXT = (".sh", ".bash", ".zsh", ".ksh")
_JS_EXT = (".mjs", ".cjs", ".js", ".ts", ".tsx", ".jsx")


# Interpreters that take a program on the command line. This tool reads python and shell;
# for anything else the body is recorded as unread rather than passed over in silence.
INLINE_EVAL = {
    "node": ("-e", "--eval"),
    "bun": ("-e", "--eval"),
    "deno": ("eval",),
    "ruby": ("-e",),
    "perl": ("-e",),
    "php": ("-r",),
}


def body_language(resource_name, interpreter):
    """Decide how to read a resource body: by its extension, else by the step's interpreter."""
    ext = os.path.splitext(resource_name)[1].lower()
    if ext in _PY_EXT:
        return "python"
    if ext in _SH_EXT:
        return "shell"
    if ext in _JS_EXT:
        return "javascript"
    if PY_INTERP.match(interpreter):
        return "python"
    if interpreter in SHELLS:
        return "shell"
    if interpreter in ("node", "deno", "bun"):
        return "javascript"
    return "unknown"


def scan_step(step, pkg_dir, reach, step_name):
    stype = step.get("type")
    if stype and str(stype).startswith("browser."):
        reach.notes.append("browser step %s needs the --full browser install" % step_name)
        return "browser"
    endpoint = step.get("endpoint")
    if not stype and endpoint:
        if not isinstance(endpoint, str):
            reach.notes.append("%s: endpoint is not a string and was not read" % step_name)
            return "other"
        return "adapter"
    if stype != "process.exec":
        return "other"
    argv = step.get("argv") or []
    if isinstance(argv, str):
        reach.notes.append("%s: argv is a string, not a list, and was not read" % step_name)
        return "process"
    if not isinstance(argv, list):
        reach.notes.append("%s: argv is not a list and was not read" % step_name)
        return "process"
    argv = [a for a in argv if isinstance(a, str)]
    if not argv:
        return "process"
    head = os.path.basename(argv[0])
    if RESOURCE_RE.search(argv[0]):
        body_args = argv  # the body itself sits in argv[0]; it is not a command
        head = ""
    else:
        reach.execs.add(head)
        body_args = argv[1:]
    resource_bodies = []
    for a in body_args:
        for r in RESOURCE_RE.findall(a):
            if os.path.basename(r) == SHIM:
                continue  # rote's own exec shim, not the Play's reach
            src, problem = read_resource(pkg_dir, r)
            if src is None:
                reach.notes.append("%s references @resource{%s}: %s" % (step_name, r, problem))
                reach.unanalysed.append({"step": step_name, "resource": r, "reason": problem})
            else:
                resource_bodies.append((r, src))
    for rname, src in resource_bodies:
        lang = body_language(rname, head)
        origin = "%s:%s" % (step_name, rname)
        if lang == "python":
            scan_python(src, reach, origin)
        elif lang == "shell":
            scan_shell(src, reach, origin)
        else:
            reach.unanalysed.append(
                {"step": step_name, "resource": rname, "reason": "body is %s, which this tool does not read" % lang}
            )
    if True:  # a step may carry a resource body and an inline -c body; read both
        if PY_INTERP.match(head) and "-c" in argv:
            i = argv.index("-c")
            if i + 1 < len(argv):
                scan_python(argv[i + 1], reach, step_name)
        elif head in SHELLS and "-c" in argv:
            i = argv.index("-c")
            if i + 1 < len(argv):
                scan_shell(argv[i + 1], reach, step_name)
        else:
            # An inline body in a language this tool does not read must be NAMED. Saying
            # nothing about it is indistinguishable from finding nothing in it, and a
            # `node -e` body that requires fs was reading the filesystem while this tool
            # reported an empty reach set.
            for flag in INLINE_EVAL.get(head, ()):
                if flag in argv:
                    i = argv.index(flag)
                    if i + 1 < len(argv) and argv[i + 1].strip():
                        reach.unanalysed.append(
                            {
                                "step": step_name,
                                "resource": "inline %s %s body" % (head, flag),
                                "reason": "this tool reads python and shell; a %s body is not read" % head,
                            }
                        )
                    break
    return "process"


def module_present(name):
    top = name.split(".")[0]
    if top in STDLIB:
        return True
    try:
        return importlib.util.find_spec(top) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


# Directories every unix has. An absolute path into one of these is portable and is not a finding.
SYSTEM_BIN_PREFIXES = (
    "/usr/bin/",
    "/bin/",
    "/usr/sbin/",
    "/sbin/",
    "/usr/local/bin/",
    "/opt/homebrew/bin/",
    "/usr/libexec/",
)
# A path under a user home is broken for everyone who is not that user, whatever it resolves to here.
HOME_PREFIXES = ("/Users/", "/home/", "/root/")


def portability_paths(steps):
    """Absolute paths in argv that a published package cannot rely on.

    `rote play inspect` resolves declared tools and says nothing about argv, so a step whose
    argv points at a file in the author's home directory reports as eligible and then cannot
    run for anybody else. Seen on real published Plays, including one where argv[0] itself is
    an interpreter inside a devcontainer venv.

    A `@resource{...}` reference is package-relative and travels in the archive, so it is fine.
    """
    out = []
    for sname, step in (steps or {}).items():
        if not isinstance(step, dict):
            continue
        for i, arg in enumerate(step.get("argv") or []):
            if not isinstance(arg, str) or not arg:
                continue
            if arg.startswith("@resource{") or arg.startswith("@"):
                continue
            if arg.startswith("~"):
                target = os.path.expanduser(arg)
            elif arg.startswith("/"):
                target = arg
            else:
                continue
            in_home = any(target.startswith(h) for h in HOME_PREFIXES) or arg.startswith("~")
            in_system = any(target.startswith(sp) for sp in SYSTEM_BIN_PREFIXES)
            if in_system and not in_home:
                continue
            exists = os.path.exists(target)
            if not in_home and exists:
                # An absolute path outside a home that resolves here is worth noting only when
                # it is clearly machine-specific; leave the quiet case alone.
                continue
            out.append(
                {
                    "step": sname,
                    "argv_index": i,
                    "path": arg,
                    "exists_here": exists,
                    "in_home_directory": in_home,
                    "why": (
                        "path inside a user home directory, so it cannot resolve for anyone else"
                        if in_home
                        else "absolute path that does not resolve on this machine"
                    ),
                }
            )
    return out


def analyze(pkg_dir, ref):
    steps, md, declared, source, load_notes = load_package(pkg_dir)
    reach = Reach()
    reach.notes.extend(load_notes)
    kinds = {}
    for sname, step in steps.items():
        if isinstance(step, dict):
            k = scan_step(step, pkg_dir, reach, sname)
            kinds[k] = kinds.get(k, 0) + 1
    adapters = sorted(
        {s.get("endpoint") for s in steps.values() if isinstance(s, dict) and not s.get("type") and s.get("endpoint")}
    )
    declared_endpoints = md.get("requires_endpoints", []) or []
    resdir = os.path.join(pkg_dir, "resources")
    local_mods = set()
    if os.path.isdir(resdir):
        # Resources nest, for example resources/scripts/helper.py, so walk rather than list.
        # A sibling module is the package's own file, not a dependency it is missing.
        # os.walk ignores errors by default, so a resources subdirectory this process cannot
        # read is skipped in silence and its modules then look like missing dependencies.
        # A false positive, and the tool says nothing about why.
        walk_errors = []
        for _dp, _dn, files in os.walk(resdir, onerror=walk_errors.append):
            for e in files:
                if e.endswith(".py"):
                    local_mods.add(e[:-3])
        for exc in walk_errors:
            where = getattr(exc, "filename", None) or str(exc)
            why = getattr(exc, "strerror", None) or type(exc).__name__
            reach.notes.append(
                "%s could not be read (%s), so a sibling module inside it may be reported as missing" % (where, why)
            )
            reach.unanalysed.append(
                {
                    "step": "-",
                    "resource": str(where),
                    "reason": "directory could not be read: %s" % why,
                }
            )
    third_party = sorted(n for n in reach.imports if n not in STDLIB and n not in local_mods)
    sibling = sorted(n for n in reach.imports if n in local_mods)
    missing_mods = []
    for n in third_party:
        if not module_present(n):
            missing_mods.append(n)
    reached = sorted(reach.execs)
    undeclared = [c for c in reached if c not in declared]
    missing_here = [c for c in reached if shutil.which(c) is None]
    declared_unreached = [c for c in declared if c not in reach.execs]
    required_missing = [c for c in declared if declared[c] and shutil.which(c) is None]
    return {
        "ref": ref,
        "read_from": source,
        "declared": sorted(declared),
        "reached": reached,
        "undeclared_but_reached": sorted(undeclared),
        "missing_here": sorted(missing_here),
        "declared_never_reached": sorted(declared_unreached),
        "required_missing_here": sorted(required_missing),
        "dynamic": sorted(reach.dynamic),
        "third_party_imports": third_party,
        "sibling_modules": sibling,
        "missing_modules": missing_mods,
        "env_vars_read": sorted(reach.envs),
        "unanalysed_bodies": reach.unanalysed,
        "writes_cwd": _dedupe_writes(reach.writes, "cwd"),
        "writes_outside_cwd": _dedupe_writes(reach.writes, "outside_cwd"),
        "adapters_reached": adapters,
        "adapters_declared": declared_endpoints,
        "needs_browser": kinds.get("browser", 0) > 0,
        "step_kinds": kinds,
        "unportable_paths": portability_paths(steps),
        "notes": reach.notes,
    }


def walk(root):
    out = []
    if not os.path.isdir(root):
        return out
    for owner in sorted(os.listdir(root)):
        od = os.path.join(root, owner)
        if owner.startswith(".") or not os.path.isdir(od):
            continue
        if is_package(od):
            out.append((od, owner))
            continue
        for name in sorted(os.listdir(od)):
            pd = os.path.join(od, name)
            if name.startswith(".") or not is_package(pd):
                continue
            out.append((pd, owner + "/" + name))
    return out


def _ref_for(pkg_dir, root):
    """owner/name for a pulled package, bare name for one authored directly in the store."""
    pkg_dir = os.path.abspath(pkg_dir)
    parent = os.path.dirname(pkg_dir)
    if os.path.abspath(os.path.expanduser(root)) == parent:
        return os.path.basename(pkg_dir)
    return os.path.basename(parent) + "/" + os.path.basename(pkg_dir)


def is_package(d):
    return os.path.isfile(os.path.join(d, "manifest.json")) or os.path.isfile(os.path.join(d, "main.ts"))


def resolve_targets(spec, root):
    root = os.path.expanduser(root)
    spec = (spec or "all").strip()
    if spec in ("", "all", "$play"):
        return walk(root), None
    cand = os.path.expanduser(spec)
    if os.path.isdir(cand) and is_package(cand):
        return [(cand, _ref_for(cand, root))], None
    if cand.endswith("main.ts") and os.path.isfile(cand):
        d = os.path.dirname(cand)
        return [(d, _ref_for(d, root))], None
    if "/" in spec and not spec.startswith((".", "/", "~")):
        owner, _, name = spec.partition("/")
        pd = os.path.join(root, owner, name)
        if is_package(pd):
            return [(pd, spec)], None
        return [], "no package for %s under %s; pull it first with rote registry play pull %s" % (spec, root, spec)
    return [], "could not resolve %r as all, an owner/name reference, or a package directory" % spec


def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "all"
    root = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("$") else "~/.rote/flows"
    strict = len(sys.argv) > 3 and sys.argv[3].lower() in ("1", "true", "yes")
    targets, problem = resolve_targets(spec, root)
    if problem:
        print(
            json.dumps({"ok": True, "available": False, "target": spec, "count": 0, "packages": [], "warning": problem})
        )
        return 0
    packages = []
    for pd, ref in targets:
        try:
            packages.append(analyze(pd, ref))
        except Exception as exc:
            packages.append(
                {"ref": ref, "unreadable": True, "warning": "could not read this package: %s" % str(exc)[:80]}
            )
    gaps = sum(len(p.get("undeclared_but_reached", [])) for p in packages)
    missing = sum(len(p.get("missing_here", [])) for p in packages)
    unportable = sum(len(p.get("unportable_paths", [])) for p in packages)
    unportable_pkgs = sum(1 for p in packages if p.get("unportable_paths"))

    # A store with hundreds of Plays produced 172 KB of stdout, which overran the step output
    # capture and made the whole run fail. Totals stay exact; only the per-package detail is
    # trimmed, to the rows a reader actually wants, and the trim is disclosed rather than silent.
    DETAIL_CAP = 60
    total_packages = len(packages)
    if total_packages > DETAIL_CAP:
        interesting = [
            p
            for p in packages
            if p.get("undeclared_but_reached") or p.get("missing_here") or p.get("unportable_paths") or p.get("notes")
        ]
        # Severity order, so the cap never trims away the worst finding. A step pointing at
        # someone else's home directory cannot run for anybody, which outranks a missing tool.
        interesting.sort(
            key=lambda p: (
                0 if p.get("unportable_paths") else 1,
                0 if p.get("missing_here") else 1,
                0 if p.get("undeclared_but_reached") else 1,
            )
        )
        rest = [p for p in packages if p not in interesting]
        packages = (interesting + rest)[:DETAIL_CAP]
        trimmed = total_packages - len(packages)
    else:
        trimmed = 0
    print(
        json.dumps(
            {
                "ok": True,
                "available": True,
                "target": spec,
                "flows_root": os.path.expanduser(root),
                "python": sys.version.split()[0],
                "stdlib_source": "api" if hasattr(sys, "stdlib_module_names") else "sysconfig-fallback",
                "count": total_packages,
                "unportable_total": unportable,
                "unportable_packages": unportable_pkgs,
                "detailed": len(packages),
                "trimmed": trimmed,
                "undeclared_total": gaps,
                "missing_total": missing,
                "packages": packages,
            }
        )
    )
    if strict and any(p.get("missing_here") for p in packages):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
