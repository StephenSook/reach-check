import json, os, shutil, subprocess, sys

offline = (len(sys.argv) > 2 and sys.argv[2].lower() in ('1', 'true', 'yes'))
root = os.path.expanduser('~/.rote/flows')
rote = shutil.which('rote')
if rote is None:
    for cand in ('~/.local/bin/rote', '~/.cargo/bin/rote'):
        c = os.path.expanduser(cand)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            rote = c
            break
if offline or rote is None:
    print(json.dumps({'ok': True, 'available': False, 'verdicts': {},
                      'warning': 'skipped: offline was requested' if offline
                                 else 'skipped: rote is not on PATH here, so its own verdict is unavailable'}))
    raise SystemExit(0)

refs = []
if os.path.isdir(root):
    for owner in sorted(os.listdir(root)):
        od = os.path.join(root, owner)
        if owner.startswith('.') or not os.path.isdir(od):
            continue
        for name in sorted(os.listdir(od)):
            if name.startswith('.'):
                continue
            if os.path.isfile(os.path.join(od, name, 'manifest.json')):
                refs.append(owner + '/' + name)

verdicts = {}
for ref in refs:
    try:
        r = subprocess.run([rote, 'play', 'inspect', ref, '--json'],
                           capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout or '{}')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        verdicts[ref] = {'available': False, 'warning': 'inspect unavailable: %s' % str(exc)[:60]}
        continue
    if not d.get('ok'):
        err = (d.get('error') or {}).get('kind') or 'inspect failed'
        verdicts[ref] = {'available': False, 'warning': str(err)[:80]}
        continue
    ex = d['data']['play_inspect']['execution']
    verdicts[ref] = {'available': True,
                     'play_run_eligible': ex.get('play_run_eligible'),
                     'blockers': ex.get('blockers') or [],
                     'privileged_access': ex.get('privileged_access')}
print(json.dumps({'ok': True, 'available': True, 'count': len(verdicts), 'verdicts': verdicts}))