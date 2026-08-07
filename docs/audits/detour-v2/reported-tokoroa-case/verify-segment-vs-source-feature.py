import time
from nzcl import db
from nzcl.detour import _isolation, _walk
from nzcl.routing import route

SNAP = 'amds-national-2026-07-28-5b359d84'
LINK = 375057

def arcs_of_links(link_ids):
    return [r['arc_id'] for r in db.query(
        'SELECT arc_id FROM arcs WHERE snapshot_id=%s AND link_id = ANY(%s) ORDER BY arc_id',
        (SNAP, list(link_ids)))]

seg_arcs = arcs_of_links([LINK])
grp_arcs = [r['arc_id'] for r in db.query(
    "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND closure_group_id='{1073a927-4c97-4c9a-b41a-bf6f5edf0cad}' ORDER BY arc_id", (SNAP,))]
print('segment arcs :', seg_arcs)
print('group arcs   :', len(grp_arcs))

u, v = 46745, 47743

for label, excl in (('SEGMENT-ONLY (375057)', seg_arcs), ('V1 SOURCE-FEATURE (17 links)', grp_arcs)):
    print()
    print('===', label, '===')
    for d in ('forward', 'reverse'):
        a, b = (u, v) if d == 'forward' else (v, u)
        t0 = time.perf_counter()
        r = route(SNAP, a, b, metric='distance', profile='car', excluded_arcs=excl)
        ms = (time.perf_counter() - t0) * 1000
        print(f'  {d:8} {a}->{b}: {r.status:14} dist={None if r.distance_m is None else round(r.distance_m,1)}  {ms:.0f}ms')
    iso = _isolation(SNAP, u, v, excl, 'car')
    print(f'  isolation(u=46745,v=47743): side={iso.side} nodes={iso.pocket_node_count} '
          f'links={iso.pocket_link_count} len={iso.pocket_length_m:.3f} exact={iso.exact}')
    isor = _isolation(SNAP, v, u, excl, 'car')
    print(f'  isolation(u=47743,v=46745): side={isor.side} nodes={isor.pocket_node_count} '
          f'links={isor.pocket_link_count} len={isor.pocket_length_m:.3f} exact={isor.exact}')

# Is the pocket reachable from node 46744 when only 375057 is shut?
print()
print('=== pocket access test: can node 46744 reach the wider network? ===')
for label, excl in (('segment-only', seg_arcs), ('source-feature', grp_arcs)):
    nodes, ok = _walk(SNAP, 46744, excl, 'car', False, 5000)
    print(f'  {label:15}: forward-reachable node count from 46744 = {len(nodes)} (terminated within bound={ok})')
