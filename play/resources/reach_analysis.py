import ast, importlib.util, json, os, re, shlex, shutil, sys



def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"')
    return v


def _inline_list(v):
    v = v.strip()
    if v.startswith('[') and v.endswith(']'):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p) for p in inner.split(',')]
    return None


def frontmatter_steps(text):
    """Read the steps: block from a @rote-frontmatter JSDoc header.

    Understands only the shapes rote emits: scalar keys, block sequences under a key,
    and inline lists. Anything deeper is recorded as a note instead of being guessed at.
    """
    notes = []
    if '@rote-frontmatter' not in text:
        return {}, {}, ['no @rote-frontmatter block in main.ts']
    body = text.split('@rote-frontmatter', 1)[1]
    lines, started = [], False
    for raw in body.splitlines():
        line = re.sub(r'^\s*\* ?', '', raw)
        if line.strip() == '---':
            if not started:
                started = True
                continue
            break
        if started:
            lines.append(line.rstrip())
    if not started:
        return {}, {}, ['frontmatter delimiters not found']

    steps, meta = {}, {}
    i = 0
    while i < len(lines) and not lines[i].startswith('steps:'):
        if lines[i].startswith('metadata:'):
            i += 1
            while i < len(lines) and lines[i].startswith('  ') and lines[i].strip():
                k, _, v = lines[i].strip().partition(':')
                meta[k.strip()] = _scalar(v)
                i += 1
            continue
        i += 1
    if i >= len(lines):
        return steps, meta, notes + ['no steps: block']
    i += 1

    cur = None
    pend_key, pend_indent = None, -1
    while i < len(lines):
        l = lines[i]
        if not l.strip():
            i += 1
            continue
        indent = len(l) - len(l.lstrip(' '))
        if indent == 0:
            break
        s = l.strip()

        if pend_key is not None and indent < pend_indent:
            pend_key, pend_indent = None, -1

        if s.startswith('-') and (s == '-' or s.startswith('- ')):
            item = s[2:] if s.startswith('- ') else ''
            block = re.match(r'^([|>])(\d*)([-+]?)$', item.strip())
            if block:
                ind_hint = int(block.group(2)) if block.group(2) else None
                buf, j = [], i + 1
                while j < len(lines):
                    nxt = lines[j]
                    if nxt.strip() == '':
                        buf.append('')
                        j += 1
                        continue
                    nind = len(nxt) - len(nxt.lstrip(' '))
                    if nind <= indent:
                        break
                    buf.append(nxt)
                    j += 1
                while buf and buf[-1] == '':
                    buf.pop()
                real = [b for b in buf if b.strip()]
                base = min((len(b) - len(b.lstrip(' '))) for b in real) if real else 0
                text = '\n'.join(b[base:] if len(b) >= base else b.lstrip(' ') for b in buf)
                chomp = block.group(3)
                if chomp == '-':
                    text = text.rstrip('\n')
                elif chomp == '+':
                    text = text + '\n'
                else:
                    text = text.rstrip('\n') + '\n'
                value = text
                i = j
            else:
                value = _scalar(item)
                i += 1
            if pend_key == 'argv' and cur is not None:
                steps[cur].setdefault('argv', []).append(value)
            elif pend_key and cur is not None:
                steps[cur].setdefault(pend_key, [])
                if isinstance(steps[cur][pend_key], list):
                    steps[cur][pend_key].append(value)
            continue

        if indent == 2 and s.endswith(':') and ': ' not in s:
            cur = s[:-1].strip()
            steps[cur] = {}
            pend_key, pend_indent = None, -1
            i += 1
            continue

        if cur is None:
            i += 1
            continue

        k, sep, v = s.partition(':')
        if not sep:
            i += 1
            continue
        k, v = k.strip(), v.strip()
        if v == '':
            pend_key, pend_indent = k, indent
            if k not in ('argv', 'depends_on'):
                notes.append('step %s: nested block %r summarised only' % (cur, k))
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
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
    except OSError:
        return out
    cur, in_tools = None, False
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        if s.startswith('[['):
            if s == '[[tools]]':
                if cur and cur.get('command'):
                    out[cur['command']] = cur.get('required', False)
                cur, in_tools = {}, True
            else:
                if cur and cur.get('command'):
                    out[cur['command']] = cur.get('required', False)
                cur, in_tools = None, False
            continue
        if s.startswith('['):
            if cur and cur.get('command'):
                out[cur['command']] = cur.get('required', False)
            cur, in_tools = None, False
            continue
        if in_tools and cur is not None and '=' in s:
            k, _, v = s.partition('=')
            k, v = k.strip(), v.strip()
            if v.lower() in ('true', 'false'):
                cur[k] = (v.lower() == 'true')
            else:
                cur[k] = v.strip('"\'')
    if cur and cur.get('command'):
        out[cur['command']] = cur.get('required', False)
    return {c: bool(r) for c, r in out.items()}


def load_package(pkg_dir):
    """Return (steps, metadata, declared, source, notes).

    A published or pulled package carries manifest.json. A package you are still writing
    carries only main.ts and deps.toml, which is the case this tool exists to cover.
    """
    mf = os.path.join(pkg_dir, 'manifest.json')
    if os.path.isfile(mf):
        with open(mf, encoding='utf-8') as fh:
            m = json.load(fh)
        md = m.get('metadata', {}) or {}
        rd = md.get('runtime_dependencies', {}) or {}
        declared = {}
        for t in rd.get('host_tools', []) or []:
            cmd = t.get('command') or t.get('id')
            if cmd:
                declared[cmd] = bool(t.get('required'))
        return m.get('steps', {}) or {}, md, declared, 'manifest.json', []
    mt = os.path.join(pkg_dir, 'main.ts')
    if os.path.isfile(mt):
        with open(mt, encoding='utf-8', errors='replace') as fh:
            steps, meta, notes = frontmatter_steps(fh.read())
        declared = read_deps_toml(os.path.join(pkg_dir, 'deps.toml'))
        return steps, meta, declared, 'main.ts frontmatter and deps.toml', notes
    return {}, {}, {}, 'nothing readable', ['no manifest.json and no main.ts in this directory']



RESOURCE_RE = re.compile(r'@resource\{([^}]+)\}')
INTERP_RE = re.compile(r'^@[A-Za-z_][A-Za-z0-9_]*\{')
SHELLS = {'sh', 'bash', 'zsh', 'dash', 'ksh'}

# Shell grammar words and shell builtins are not reached executables. A Play that runs `echo`
# is not depending on /bin/echo, and reporting `fi` or `then` as a dependency would be a false
# claim about somebody else's package, which is the worst failure this tool can have.
SHELL_KEYWORDS = {
    'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until', 'do', 'done', 'case', 'esac',
    'in', 'function', 'select', 'time', 'coproc', '{', '}', '(', ')', '[[', ']]', '!', ';;', '&',
}
SHELL_BUILTINS = {
    'alias', 'bg', 'bind', 'break', 'builtin', 'caller', 'cd', 'command', 'compgen', 'complete',
    'compopt', 'continue', 'declare', 'dirs', 'disown', 'echo', 'enable', 'eval', 'exec', 'exit',
    'export', 'false', 'fc', 'fg', 'getopts', 'hash', 'help', 'history', 'jobs', 'let', 'local',
    'logout', 'mapfile', 'popd', 'printf', 'pushd', 'pwd', 'read', 'readarray', 'readonly',
    'return', 'set', 'shift', 'shopt', 'source', 'suspend', 'test', 'times', 'trap', 'true',
    'type', 'typeset', 'ulimit', 'umask', 'unalias', 'unset', 'wait', ':', '.', '[',
}
_CMD_OK = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.+-]*$')
_ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def plausible_command(tok):
    """True only for tokens that can really be a command name.

    Deliberately conservative. An assignment, a flag, a glob, a variable, a redirection or a
    grammar word is not a command, and when in doubt this returns False so the tool stays quiet
    rather than inventing a dependency.
    """
    if not tok or tok in SHELL_KEYWORDS or tok in SHELL_BUILTINS:
        return False
    if '=' in tok or tok.startswith('-') or tok.startswith('$'):
        return False
    if any(ch in tok for ch in '*?[]{}()<>|&;`"\'' + chr(92)):
        return False
    base = os.path.basename(tok)
    if base in SHELL_BUILTINS or base in SHELL_KEYWORDS:
        return False
    return bool(_CMD_OK.match(base))
PY_INTERP = re.compile(r'^python3(\.\d+)?$|^python$')
PROC_CALLS = {'run', 'call', 'check_call', 'check_output', 'Popen'}
WRITE_CMDS = {'mkdir', 'tee', 'cp', 'mv', 'rm', 'touch', 'ln', 'dd', 'truncate', 'install', 'rsync'}
SHIM = '_rote_python_c.py'


def stdlib_names():
    names = getattr(sys, 'stdlib_module_names', None)
    if names:
        return set(names)
    import sysconfig
    out = set(sys.builtin_module_names)
    try:
        d = sysconfig.get_paths()['stdlib']
        for e in os.listdir(d):
            if e.endswith('.py'):
                out.add(e[:-3])
            elif os.path.isdir(os.path.join(d, e)) and os.path.exists(os.path.join(d, e, '__init__.py')):
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
        self.writes = []          # (command, target, scope)
        self.unanalysed = []      # bodies this tool cannot read, named rather than skipped
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
            return {vals[0]}, False          # a bound argv list: the command is its head
        return set(), True
    return set(), True


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return '.'.join(reversed(parts))


def scan_python(src, reach, origin, depth=0):
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        reach.notes.append('unparseable python in %s: %s' % (origin, str(exc)[:60]))
        return
    assigned, iterated = _collect_consts(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                reach.imports.add(a.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                reach.imports.add(node.module.split('.')[0])
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            tail = name.rsplit('.', 1)[-1]
            if name in ('os.system', 'os.popen'):
                if node.args:
                    s = _const_str(node.args[0])
                    if s:
                        scan_shell(s, reach, origin, depth)
                    else:
                        reach.dynamic.add('%s(<computed>)' % name)
            elif tail in PROC_CALLS and ('subprocess' in name or tail == 'Popen'):
                if node.args:
                    got, dyn = _resolve_argv0(node.args[0], assigned, iterated)
                    reach.execs |= {c for c in got if plausible_command(c)}
                    if dyn:
                        reach.dynamic.add('%s(<computed>)' % name)
            elif name in ('shutil.which',) and node.args:
                got, dyn = _resolve_argv0(node.args[0], assigned, iterated)
                reach.execs |= {c for c in got if plausible_command(c)}
                if dyn:
                    reach.dynamic.add('shutil.which(<computed>)')
            elif name in ('os.getenv', 'os.environ.get') and node.args:
                s = _const_str(node.args[0])
                if s:
                    reach.envs.add(s)
        elif isinstance(node, ast.Subscript):
            if _dotted(node.value) == 'os.environ':
                s = _const_str(node.slice)
                if s:
                    reach.envs.add(s)


def _classify_write(head, tokens, reach):
    targets = [t for t in tokens[1:] if not t.startswith('-')]
    tgt = targets[-1] if targets else None
    scope = 'cwd'
    if tgt and (tgt.startswith('/') or tgt.startswith('~') or tgt.startswith('..')):
        scope = 'outside_cwd'
    reach.writes.append({'command': head, 'target': tgt, 'scope': scope})


def _tokens(text):
    """Tokenise one chunk of shell. Returns None when the chunk cannot be lexed."""
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return None


_SEPS = {';', '|', '||', '&&', '&', ';;'}


def _scan_tokens(toks, reach, origin, depth):
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
        if '>' in seg or '>>' in seg:
            reach.writes.append({'command': 'redirect', 'target': None, 'scope': 'cwd'})
        if seg[0] in ('for', 'select', 'case'):
            continue          # the loop variable and its word list are data, not commands
        idx = 0
        while idx < len(seg) and (seg[idx] in SHELL_KEYWORDS or _ASSIGN_RE.match(seg[idx])):
            idx += 1
        if idx >= len(seg):
            continue
        head = seg[idx]
        seg = seg[idx:]
        if not plausible_command(head):
            continue
        base = os.path.basename(head)
        reach.execs.add(base)
        if base in WRITE_CMDS:
            _classify_write(base, seg, reach)
        if PY_INTERP.match(base) and '-c' in seg:
            i = seg.index('-c')
            if i + 1 < len(seg):
                scan_python(seg[i + 1], reach, origin + '>python -c', depth + 1)
        elif base in SHELLS and '-c' in seg:
            i = seg.index('-c')
            if i + 1 < len(seg):
                scan_shell(seg[i + 1], reach, origin + '>sh -c', depth + 1)


def scan_shell(src, reach, origin, depth=0):
    """Read a shell body two ways and union the result.

    The whole-source pass keeps a multi-line quoted body, such as a nested python3 -c, intact.
    The per-line pass exists because shlex treats a newline as ordinary whitespace, so without it
    a real script collapses into one segment and every command after the first is lost. Both
    passes feed the same conservative command filter, so the union cannot invent a dependency.
    """
    if depth > 4:
        reach.notes.append('shell nesting too deep in %s' % origin)
        return
    whole = _tokens(src)
    if whole is not None:
        _scan_tokens(whole, reach, origin, depth)
    if '\n' in src:
        unlexable = 0
        for line in src.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            toks = _tokens(line)
            if toks is None:
                unlexable += 1
                continue
            _scan_tokens(toks, reach, origin, depth)
        if unlexable:
            reach.notes.append('%s: %d line(s) could not be tokenised and were skipped'
                               % (origin, unlexable))
    elif whole is None:
        reach.notes.append('unlexable shell in %s' % origin)


def read_resource(pkg_dir, name):
    """Read resources/<name>, refusing anything that escapes the package directory.

    A package is untrusted input, so a reference like @resource{../../etc/passwd} must not
    be followed. Returns (text, problem); exactly one of the two is None.
    """
    base = os.path.realpath(os.path.join(pkg_dir, 'resources'))
    target = os.path.realpath(os.path.join(base, name))
    if target != base and not target.startswith(base + os.sep):
        return None, 'refused: the reference escapes the package directory'
    if not os.path.isfile(target):
        return None, 'missing or unreadable in the package'
    try:
        with open(target, encoding='utf-8', errors='replace') as fh:
            return fh.read(), None
    except OSError as exc:
        return None, 'unreadable: %s' % str(exc)[:50]


_PY_EXT = ('.py',)
_SH_EXT = ('.sh', '.bash', '.zsh', '.ksh')
_JS_EXT = ('.mjs', '.cjs', '.js', '.ts', '.tsx', '.jsx')


def body_language(resource_name, interpreter):
    """Decide how to read a resource body: by its extension, else by the step's interpreter."""
    ext = os.path.splitext(resource_name)[1].lower()
    if ext in _PY_EXT:
        return 'python'
    if ext in _SH_EXT:
        return 'shell'
    if ext in _JS_EXT:
        return 'javascript'
    if PY_INTERP.match(interpreter):
        return 'python'
    if interpreter in SHELLS:
        return 'shell'
    if interpreter in ('node', 'deno', 'bun'):
        return 'javascript'
    return 'unknown'


def scan_step(step, pkg_dir, reach, step_name):
    stype = step.get('type')
    if stype and str(stype).startswith('browser.'):
        reach.notes.append('browser step %s needs the --full browser install' % step_name)
        return 'browser'
    if not stype and step.get('endpoint'):
        return 'adapter'
    if stype != 'process.exec':
        return 'other'
    argv = step.get('argv') or []
    argv = [a for a in argv if isinstance(a, str)]
    if not argv:
        return 'process'
    head = os.path.basename(argv[0])
    reach.execs.add(head)
    body_args = argv[1:]
    resource_bodies = []
    for a in body_args:
        for r in RESOURCE_RE.findall(a):
            if os.path.basename(r) == SHIM:
                continue                      # rote's own exec shim, not the Play's reach
            src, problem = read_resource(pkg_dir, r)
            if src is None:
                reach.notes.append('%s references @resource{%s}: %s' % (step_name, r, problem))
                reach.unanalysed.append({'step': step_name, 'resource': r, 'reason': problem})
            else:
                resource_bodies.append((r, src))
    for rname, src in resource_bodies:
        lang = body_language(rname, head)
        origin = '%s:%s' % (step_name, rname)
        if lang == 'python':
            scan_python(src, reach, origin)
        elif lang == 'shell':
            scan_shell(src, reach, origin)
        else:
            reach.unanalysed.append({'step': step_name, 'resource': rname,
                                     'reason': 'body is %s, which this tool does not read' % lang})
    if not resource_bodies:
        if PY_INTERP.match(head) and '-c' in argv:
            i = argv.index('-c')
            if i + 1 < len(argv):
                scan_python(argv[i + 1], reach, step_name)
        elif head in SHELLS and '-c' in argv:
            i = argv.index('-c')
            if i + 1 < len(argv):
                scan_shell(argv[i + 1], reach, step_name)
    return 'process'


def module_present(name):
    top = name.split('.')[0]
    if top in STDLIB:
        return True, 'stdlib'
    try:
        return (importlib.util.find_spec(top) is not None), 'site'
    except (ImportError, ValueError, ModuleNotFoundError):
        return False, 'site'

def analyze(pkg_dir, ref):
    steps, md, declared, source, load_notes = load_package(pkg_dir)
    reach = Reach()
    reach.notes.extend(load_notes)
    kinds = {}
    for sname, step in steps.items():
        if isinstance(step, dict):
            k = scan_step(step, pkg_dir, reach, sname)
            kinds[k] = kinds.get(k, 0) + 1
    adapters = sorted({s.get('endpoint') for s in steps.values()
                       if isinstance(s, dict) and not s.get('type') and s.get('endpoint')})
    declared_endpoints = md.get('requires_endpoints', []) or []
    resdir = os.path.join(pkg_dir, 'resources')
    local_mods = set()
    if os.path.isdir(resdir):
        # Resources nest, for example resources/scripts/helper.py, so walk rather than list.
        # A sibling module is the package's own file, not a dependency it is missing.
        for _dp, _dn, files in os.walk(resdir):
            for e in files:
                if e.endswith('.py'):
                    local_mods.add(e[:-3])
    third_party = sorted(n for n in reach.imports if n not in STDLIB and n not in local_mods)
    sibling = sorted(n for n in reach.imports if n in local_mods)
    missing_mods = []
    for n in third_party:
        ok, _ = module_present(n)
        if not ok:
            missing_mods.append(n)
    reached = sorted(reach.execs)
    undeclared = [c for c in reached if c not in declared]
    missing_here = [c for c in reached if shutil.which(c) is None]
    declared_unreached = [c for c in declared if c not in reach.execs]
    required_missing = [c for c in declared if declared[c] and shutil.which(c) is None]
    return {
        'ref': ref,
        'read_from': source,
        'declared': sorted(declared),
        'reached': reached,
        'undeclared_but_reached': sorted(undeclared),
        'missing_here': sorted(missing_here),
        'declared_never_reached': sorted(declared_unreached),
        'required_missing_here': sorted(required_missing),
        'dynamic': sorted(reach.dynamic),
        'third_party_imports': third_party,
        'sibling_modules': sibling,
        'missing_modules': missing_mods,
        'env_vars_read': sorted(reach.envs),
        'unanalysed_bodies': reach.unanalysed,
        'writes_cwd': [w for w in reach.writes if w['scope'] == 'cwd'],
        'writes_outside_cwd': [w for w in reach.writes if w['scope'] == 'outside_cwd'],
        'adapters_reached': adapters,
        'adapters_declared': declared_endpoints,
        'needs_browser': kinds.get('browser', 0) > 0,
        'step_kinds': kinds,
        'notes': reach.notes,
    }

def walk(root):
    out = []
    if not os.path.isdir(root):
        return out
    for owner in sorted(os.listdir(root)):
        od = os.path.join(root, owner)
        if owner.startswith('.') or not os.path.isdir(od):
            continue
        if is_package(od):
            out.append((od, owner))
            continue
        for name in sorted(os.listdir(od)):
            pd = os.path.join(od, name)
            if name.startswith('.') or not is_package(pd):
                continue
            out.append((pd, owner + '/' + name))
    return out





def _ref_for(pkg_dir, root):
    """owner/name for a pulled package, bare name for one authored directly in the store."""
    pkg_dir = os.path.abspath(pkg_dir)
    parent = os.path.dirname(pkg_dir)
    if os.path.abspath(os.path.expanduser(root)) == parent:
        return os.path.basename(pkg_dir)
    return os.path.basename(parent) + '/' + os.path.basename(pkg_dir)

def is_package(d):
    return os.path.isfile(os.path.join(d, 'manifest.json')) or os.path.isfile(os.path.join(d, 'main.ts'))

def resolve_targets(spec, root):
    root = os.path.expanduser(root)
    spec = (spec or 'all').strip()
    if spec in ('', 'all', '$play'):
        return walk(root), None
    cand = os.path.expanduser(spec)
    if os.path.isdir(cand) and is_package(cand):
        return [(cand, _ref_for(cand, root))], None
    if cand.endswith('main.ts') and os.path.isfile(cand):
        d = os.path.dirname(cand)
        return [(d, _ref_for(d, root))], None
    if '/' in spec and not spec.startswith(('.', '/', '~')):
        owner, _, name = spec.partition('/')
        pd = os.path.join(root, owner, name)
        if is_package(pd):
            return [(pd, spec)], None
        return [], 'no package for %s under %s; pull it first with rote registry play pull %s' % (spec, root, spec)
    return [], 'could not resolve %r as all, an owner/name reference, or a package directory' % spec


def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else 'all'
    root = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('$') else '~/.rote/flows'
    strict = (len(sys.argv) > 3 and sys.argv[3].lower() in ('1', 'true', 'yes'))
    targets, problem = resolve_targets(spec, root)
    if problem:
        print(json.dumps({'ok': True, 'available': False, 'target': spec, 'count': 0,
                          'packages': [], 'warning': problem}))
        return 0
    packages = []
    for pd, ref in targets:
        try:
            packages.append(analyze(pd, ref))
        except (OSError, ValueError) as exc:
            packages.append({'ref': ref, 'unreadable': True,
                             'warning': 'could not read this package: %s' % str(exc)[:80]})
    gaps = sum(len(p.get('undeclared_but_reached', [])) for p in packages)
    missing = sum(len(p.get('missing_here', [])) for p in packages)
    print(json.dumps({'ok': True, 'available': True, 'target': spec, 'flows_root': os.path.expanduser(root),
                      'python': sys.version.split()[0],
                      'stdlib_source': 'api' if hasattr(sys, 'stdlib_module_names') else 'sysconfig-fallback',
                      'count': len(packages), 'undeclared_total': gaps, 'missing_total': missing,
                      'packages': packages}))
    if strict and any(p.get('missing_here') for p in packages):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
