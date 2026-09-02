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
    print(json.dumps({'ok': True, 'available': False, 'flows_root': root, 'count': 0, 'packages': [],
                      'warning': 'no rote flows directory at this path; nothing is installed here'}))
    raise SystemExit(0)

rows = []
for owner in sorted(os.listdir(root)):
    od = os.path.join(root, owner)
    if owner.startswith('.') or not os.path.isdir(od):
        continue
    if owner_filter and owner != owner_filter:
        continue
    for name in sorted(os.listdir(od)):
        pd = os.path.join(od, name)
        if name.startswith('.') or not os.path.isdir(pd):
            continue
        if not os.path.isfile(os.path.join(pd, 'manifest.json')):
            continue
        row = {'ref': owner + '/' + name, 'version': None, 'pulled_at': None, 'source': None}
        src = os.path.join(pd, '.rote-source')
        if os.path.isfile(src):
            try:
                with open(src, encoding='utf-8') as fh:
                    s = json.load(fh)
                row['version'] = s.get('artifact_version')
                row['pulled_at'] = s.get('pulled_at')
                row['source'] = s.get('source')
            except (OSError, ValueError) as exc:
                row['warning'] = 'unreadable .rote-source: ' + str(exc)[:60]
        else:
            row['warning'] = 'no .rote-source; authored here rather than pulled'
        rows.append(row)

print(json.dumps({'ok': True, 'available': True, 'flows_root': root,
                  'owner_filter': owner_filter or None, 'count': len(rows), 'packages': rows}))
