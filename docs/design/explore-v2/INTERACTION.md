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
| 0 ms | Result numbers count up from zero over 260 ms, ease-out, `tabular-nums` so digits do not jitter |
| 0 ms | Route draws in travel order: `stroke-dashoffset` animates from full length to zero over 520 ms |
| 220 ms | Corridor anchor rings scale in at each end |
| 520 ms | Map eases to the combined bounds |

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
| Numbers | Cross-fade rather than count up — this is a switch, not a new calculation |
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
| 200 ms | The map transitions rather than emptying: the stranded pocket fades in as a hatched amber region |
| Inspector | The hero figure switches from added distance to **what is cut off** — road length and link count stranded |
| Corridor | Where a corridor path exists, it draws as normal and the panel explains that the endpoint measure is undefined on one-way carriageways |

The distinction between a stranded driveway and a stranded settlement is carried
by the number, and by the visible extent of the hatched region.

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

- no route draw — the path appears complete
- no count-up — final figures render directly
- no inspector slide — it appears in place
- map framing jumps rather than eases
- cross-fades become instant swaps

Every state remains reachable and distinguishable. Nothing depends on animation
to be understood — the dashed comparison stroke, the amber status pill and the
hatched isolation region all read as static properties.
