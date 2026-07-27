# Explore — interaction and motion storyboard

A static screen cannot show whether the product feels immediate. This specifies
what happens between states, and is part of the design specification rather than
implementation detail to be improvised later.

Two rules govern everything below:

- **Feedback precedes computation.** Selection, highlighting and framing happen
  on the click. The result arrives when it arrives.
- **A stale number is worse than no number.** Any figure that no longer matches
  the current controls is cleared or visibly marked, never left sitting under
  changed settings.

Durations are the target feel, not a spec to hit exactly. Every one is skipped
entirely under `prefers-reduced-motion: reduce`, which jumps straight to the end
state.

---

## 1. Hover a road

| | |
| --- | --- |
| Trigger | Pointer enters the fat invisible hit line |
| Immediate | Segment brightens; cursor becomes a pointer |
| 120 ms | Compact readout fades in near the cursor: road name, route number, RCA, length |
| Leave | Readout fades out over 100 ms; brightness returns |

The hit target is a 14 px transparent line over a 1–3 px visible one, so
selection never demands pixel precision. The readout is positioned to avoid the
inspector edge.

---

## 2. Select a road

| | |
| --- | --- |
| 0 ms | Segment turns closure red with a soft halo. This is synchronous — it does not wait for the API |
| 0 ms | Inspector slides in 320 px → 0 over 180 ms, ease-out, with a 120 ms opacity fade |
| 0 ms | Any previous result is **cleared**, not left behind |
| 0 ms | URL updates via `pushState` so Back returns to the previous selection |
| ~40 ms | Map begins easing to frame the closure |

The inspector shows the road name and a skeleton for the measures immediately,
so the panel does not appear empty while the request is in flight.

---

## 3. Calculating

| | |
| --- | --- |
| < 250 ms | No spinner. A spinner that flashes for 150 ms is noise |
| ≥ 250 ms | Measure rows show a shimmer skeleton; the status pill reads `Calculating` |
| ≥ 4 s | Pill adds *"still working — long detours take longer"* |
| Superseded | The in-flight request is aborted via `AbortSignal`; a late response for a link the user has moved on from is discarded |

Measured p95 is 276 ms, so most selections resolve just past the skeleton
threshold. The threshold exists for the tail, not the median.

---

## 4. Route reveal

| | |
| --- | --- |
| 0 ms | Result figures **reveal**, they do not count up: 140 ms opacity 0→1 combined with a 4 px upward translate, ease-out |
| 0 ms | Route draws in travel order over 520 ms, ease-out |
| 220 ms | Corridor anchor rings scale in at each end |
| 520 ms | Map eases to the combined bounds |

**No count-up.** A number that spins through wrong values on the way to the
right one is a stale number with a motion curve attached, and it conflicts with
the second rule above. The reveal is 120–180 ms — long enough to register that
the value is new, too short to read the intermediate state. `tabular-nums`
still applies, so the reveal does not reflow.

### Drawing the route in MapLibre

`stroke-dashoffset` is an SVG technique and does not exist in MapLibre GL. The
feasible equivalent is an animated `line-gradient`:

1. **Merge the arcs into one `LineString`.** `line-gradient` is driven by
   `line-progress`, which MapLibre only computes when the source sets
   `lineMetrics: true` — and it measures progress along a *single* feature.
   A 327-feature collection has 327 independent progress ramps, so it must be
   concatenated first.
2. **Order and orientation are already correct on the wire.**
   `/api/v1/links/{ref}/detour` returns route arcs in path order with an
   explicit `order` property, and each arc's geometry is emitted already
   oriented in travel direction — the SQL applies `ST_Reverse(l.geom_4326)`
   when `a.direction = 'reverse'`. The client therefore concatenates in array
   order and drops the first coordinate of every arc after the first, since it
   duplicates the previous arc's last vertex. No geometric snapping, no
   tolerance, no re-derivation of direction on the client.
3. **Animate the gradient stop.** Hold a `t` from 0→1 over 520 ms on `rAF` and
   set the paint property each frame:

   ```js
   map.setPaintProperty('route', 'line-gradient', [
     'interpolate', ['linear'], ['line-progress'],
     0,               routeColour,
     Math.max(t - 0.001, 0), routeColour,
     t,               'rgba(0,0,0,0)',
     1,               'rgba(0,0,0,0)',
   ])
   ```

   The stop pair must stay strictly increasing or MapLibre rejects the
   expression, hence the clamped epsilon.

**Constraints this imposes**, worth knowing before implementation:

- `line-gradient` cannot be combined with `line-dasharray` on the same layer.
  The forward comparison route is dashed, so it is a **separate layer** with a
  flat colour and no reveal animation — it appears complete. Only the focused
  route animates, which is also the correct emphasis.
- Merging discards per-arc properties, so the hover-a-route-segment readout
  reads from a second, non-animated, fully transparent hit layer that keeps the
  original feature collection.

Drawing in travel order communicates direction without arrowheads and makes a
327-link route legible as a path rather than a shape that simply appears.

**Framing accounts for the inspector.** `fitBounds` is given a right padding
equal to the inspector width, so the route is centred in the *visible* map, not
underneath the panel.

---

## 5. Forward / reverse / compare

| | |
| --- | --- |
| Forward → Reverse | Focused teal cross-fades to the other geometry over 200 ms; the outgoing route simultaneously drops to the amber comparison treatment |
| Numbers | Cross-fade in place, without the upward translate — this is a switch between two already-computed results, not a new calculation |
| Compare | Both routes shown; the active one keeps full weight, the other drops to 60% opacity and a dashed stroke |
| Overlap | Where the two share geometry the comparison route is offset ~3 px perpendicular, so a shared corridor reads as two lines rather than one muddy one |

Compare never renders both at equal weight. One direction is always dominant.

---

## 6. Change closure scope, measure or vehicle

| | |
| --- | --- |
| 0 ms | Affected measures immediately go to skeleton — they no longer describe the current settings |
| 0 ms | The closure highlight updates at once: switching to `AMDS feature` extends red across every link in the group, and the map badge count updates |
| 0 ms | Previous route dims to 25% while the new one computes, then cross-fades |

This is the case the current build gets wrong: it leaves the old result visible
under new controls, which reads as an answer to a question the user did not ask.

---

## 7. Disconnected → isolation

A `DISCONNECTED` result is a finding, not a failure, and must not look like an
error.

| | |
| --- | --- |
| 0 ms | Status pill becomes amber `No replacement path`, never red — red is reserved for the closure |
| 200 ms | The map transitions rather than emptying: **the stranded links themselves** fade to amber over 200 ms, and the rest of the network drops to 35% |
| 260 ms | A single leader-lined label sits at the stranded set's centroid: `14 links · 6.2 km stranded` |
| Inspector | The hero figure switches from added distance to **what is cut off** — stranded road length and link count |
| Corridor | Where a corridor path exists, it draws as normal and the panel explains that the endpoint measure is undefined on one-way carriageways |

**No affected-area polygon.** An earlier version of this storyboard called for a
hatched amber region. That is wrong and it is now explicitly ruled out. The
engine identifies a set of *links* that lose connectivity — it does not compute
a service area, a catchment, or a population footprint. Any polygon drawn around
those links would be a convex or concave hull invented by the renderer, and it
would read as "this area is cut off", which is a claim the analysis does not
make: the hull would enclose properties reachable by roads outside the stranded
set. Colouring the actual stranded links claims exactly what was computed and
nothing beyond it.

The distinction between a stranded driveway and a stranded settlement is carried
by the number and by how much amber geometry is visibly on screen — which is a
direct, honest read of the stranded set's extent rather than a derived shape.

---

## 8. Copy permalink

| | |
| --- | --- |
| 0 ms | Button label swaps to `Copied` with a check |
| 1600 ms | Reverts |
| Failure | Falls back to selecting the URL in a revealed field, with a short explanation — never a silent no-op |

---

## 9. Search

| | |
| --- | --- |
| Typing | Debounced 180 ms |
| Results | Fade in; arrow keys move a highlight; Enter selects; Escape closes and restores focus |
| Highlight | On hover or keyboard focus, the candidate road brightens on the map *before* selection |
| Empty | Explains what can be searched — road name, route number, RCA, AMDS id — rather than showing "No results" alone |

Results are real buttons, not clickable `div`s, so keyboard and screen-reader
users get them for free.

---

## 10. Reduced motion

With `prefers-reduced-motion: reduce`:

- no route draw — `line-gradient` is set to the solid end state in one assignment
  and the `rAF` loop never starts
- no figure reveal — final values render directly
- no inspector slide — it appears in place
- map framing jumps rather than eases
- cross-fades become instant swaps

Every state remains reachable and distinguishable. Nothing depends on animation
to be understood — the dashed comparison stroke, the amber status pill and the
amber stranded links all read as static properties.
