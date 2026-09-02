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
SHELLS = {'sh', 'bash', 'zsh', 'dash'}
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
        self.notes = []


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_consts(tree):
    """Module-level string and list-of-string bindings, plus for-loop targets over list literals."""
    env = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            vals = None
            v = node.value
            s = _const_str(v)
            if s is not None:
                vals = [s]
            elif isinstance(v, (ast.List, ast.Tuple, ast.Set)):
                items = [_const_str(e) for e in v.elts]
                if items and all(i is not None for i in items):
                    vals = items
            if vals:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        env.setdefault(t.id, set()).update(vals)
        elif isinstance(node, ast.For):
            it = node.iter
            items = None
            if isinstance(it, (ast.List, ast.Tuple, ast.Set)):
                cand = [_const_str(e) for e in it.elts]
                if cand and all(c is not None for c in cand):
                    items = cand
            elif isinstance(it, ast.Name):
                items = sorted(env.get(it.id, []))
            if items and isinstance(node.target, ast.Name):
                env.setdefault(node.target.id, set()).update(items)
    return env


def _resolve_argv0(node, env):
    """Return (set_of_names, is_dynamic) for the executable position of a call."""
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        node = node.elts[0]
    s = _const_str(node)
    if s is not None:
        return {s}, False
    if isinstance(node, ast.Name):
        vals = env.get(node.id)
        if vals:
            return set(vals), False
        return set(), True
    if isinstance(node, ast.JoinedStr):
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
    env = _collect_consts(tree)
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
                    got, dyn = _resolve_argv0(node.args[0], env)
                    reach.execs |= got
                    if dyn:
                        reach.dynamic.add('%s(<computed>)' % name)
            elif name in ('shutil.which',) and node.args:
                got, dyn = _resolve_argv0(node.args[0], env)
                reach.execs |= got
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


def scan_shell(src, reach, origin, depth=0):
    if depth > 4:
        reach.notes.append('shell nesting too deep in %s' % origin)
        return
    try:
        lex = shlex.shlex(src, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError as exc:
        reach.notes.append('unlexable shell in %s: %s' % (origin, str(exc)[:60]))
        return
    seps = {';', '|', '||', '&&', '&', '\n'}
    segs, cur = [], []
    for t in toks:
        if t in seps:
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
        head = seg[0]
        if '>' in seg or '>>' in seg:
            reach.writes.append({'command': 'redirect', 'target': None, 'scope': 'cwd'})
        if head in ('>', '>>'):
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


def read_resource(pkg_dir, name):
    p = os.path.join(pkg_dir, 'resources', name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return None


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
            if r == SHIM:
                continue                      # rote's own exec shim, not the Play's reach
            src = read_resource(pkg_dir, r)
            if src is None:
                reach.notes.append('%s references @resource{%s} which is missing' % (step_name, r))
            else:
                resource_bodies.append((r, src))
    for rname, src in resource_bodies:
        scan_python(src, reach, '%s:%s' % (step_name, rname))
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
        for e in os.listdir(resdir):
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
