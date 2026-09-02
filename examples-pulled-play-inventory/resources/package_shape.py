import json, os, sys

def arg(i, fallback):
    if len(sys.argv) > i:
        v = sys.argv[i]
        if v and not v.startswith('$'):
            return v
    return fallback

root = os.path.expanduser(arg(1, '~/.rote/flows'))
owner_filter = arg(2, '')

if not os.path.isdir(root):
    print(json.dumps({'ok': True, 'available': False, 'flows_root': root, 'count': 0, 'detail': [],
                      'warning': 'no rote flows directory at this path; nothing is installed here'}))
    raise SystemExit(0)

out = []
for owner in sorted(os.listdir(root)):
    od = os.path.join(root, owner)
    if owner.startswith('.') or not os.path.isdir(od):
        continue
    if owner_filter and owner != owner_filter:
        continue
    for name in sorted(os.listdir(od)):
        pd = os.path.join(od, name)
        mf = os.path.join(pd, 'manifest.json')
        if name.startswith('.') or not os.path.isfile(mf):
            continue
        rec = {'ref': owner + '/' + name, 'title': None, 'summary': None, 'steps': None}
        try:
            with open(mf, encoding='utf-8') as fh:
                m = json.load(fh)
            rec['title'] = m.get('name')
            rec['summary'] = (m.get('description') or '')[:100]
            rec['steps'] = len(m.get('steps') or {})
        except (OSError, ValueError) as exc:
            rec['warning'] = 'manifest unreadable: ' + str(exc)[:60]
        total = 0
        for dp, _dn, fn in os.walk(pd):
            for f in fn:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        rec['bytes'] = total
        out.append(rec)

print(json.dumps({'ok': True, 'available': True, 'flows_root': root,
                  'owner_filter': owner_filter or None, 'count': len(out), 'detail': out}))
