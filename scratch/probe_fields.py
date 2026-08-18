import json
import sys

d = json.load(open('data/source-metadata/amds/2026-07-27/discovery-report.json'))
layers = d['layers']
want = {int(a) for a in sys.argv[1:]} or {1, 4}
for L in layers:
    if L.get('id') not in want:
        continue
    print('=' * 78)
    print(f"LAYER {L['id']}  {L.get('name')}  geom={L.get('geometryType')}")
    for k in ('hasZ', 'hasM', 'supportsReturningQueryGeometry', 'extent'):
        if k in L:
            v = L[k]
            print(f"  {k}: {json.dumps(v)[:200]}")
    for f in L.get('fields', []):
        dom = f.get('domain')
        line = f"  - {f.get('name'):<38} {f.get('type','')[13:]:<10} {f.get('alias','')}"
        print(line)
        if dom and dom.get('codedValues'):
            for cv in dom['codedValues']:
                print(f"        {cv.get('code')!r:>6} = {cv.get('name')}")
        elif dom:
            print(f"        domain: {json.dumps(dom)[:160]}")
