# Explore — design checkpoint 1

Three visual directions for the Explore screen. **C is the one to build**; A and
B are kept because C is a synthesis of them and the comparison is the argument
for it.

| File | What it shows |
| --- | --- |
| `explore-concepts.html` | Live source. Toggles direction (A/B/C) and viewport (1440/1280). |
| **`graphite-atlas-1440x900.png`** | **Direction C, desktop** |
| **`graphite-atlas-1280x800.png`** | **Direction C, laptop** |
| **`graphite-atlas-inspector-detail.png`** | **Direction C inspector, 1:1** |
| `graphite-1440x900.png` | Direction A, desktop |
| `hybrid-atlas-1440x900.png` | Direction B, desktop |
| `graphite-1280x800.png` | Direction A, laptop |
| `hybrid-atlas-1280x800.png` | Direction B, laptop |
| `graphite-inspector-detail.png` | Direction A inspector, 1:1 |
| `hybrid-atlas-inspector-detail.png` | Direction B inspector, 1:1 |

Regenerate: serve this directory over HTTP and open
`?dir=a|b|c&size=desktop|laptop&bare=1`. `bare=1` strips the page chrome so the
1440×900 frame can be captured on its own. The browser viewport must be at least
1520 px wide or the 1440 px frame is clipped and the unpainted region captures
as blank.

---

## The data is real

Every figure is the reconciled result for **Moonshine Road**, Porirua City
Council, from snapshot `amds-wellington-2026-07-27-6ef785ad`.

| Measure | Forward | Reverse |
| --- | --- | --- |
| Added distance | +20.9 km | **+53.5 km** |
| Replacement path | 22.83 km | **55.45 km** |
| Detour multiplier | 11.59× | **28.14×** |
| Added time (estimated) | 14 min | **56 min** |
| Route links | 67 | **327** |

Selected link 1,970.7 m, two-way. Network penalty 53,479 m. Flags
`SPEED_ESTIMATED`, `CLIPPED_EXTRACT`. Algorithm `pgr-dijkstra-arc 2.0.0`.

**The brief's "approximately 40 min" was wrong** — the calculated value is
3,376.3 s, i.e. 56 min. The real figure is used.

### What the numbers are not

The scope line in the inspector states this plainly, because the distinction
matters:

- **Closure scope is AMDS source feature**, both represented directions. It
  removes every graph link derived from one AMDS feature, which is not
  necessarily a whole physical road. A segment-level scope is not yet
  implemented, so the control shows `AMDS feature` selected rather than
  `Segment`.
- **Added time is estimated.** AMDS publishes no speed attribute.
- **The snapshot is the reconciled pre-hardening pilot baseline.** Once
  segment scope and exact turn-restriction handling land, the algorithm version
  changes and these figures must be regenerated.

---

## What changed from the current build

The existing interface leads with AMDS id, internal link id and RCA metadata,
and puts the result below the fold. All three concepts invert that:

1. road name
2. direction and status
3. **added distance** — the answer, at 52–58 px
4. replacement path, multiplier, added time
5. forward/reverse comparison
6. scenario controls
7. scope and confidence
8. *then* route breakdown, link attributes, quality flags, and finally AMDS ids,
   snapshot and algorithm under `Source & methodology`

Forward and reverse are no longer two identical stacked cards. Reverse is the
focused teal route; forward is a held-back amber dashed line plus a three-column
table — so there is no green/amber tangle to read through.

---

## Direction C — Graphite Atlas

C is not a third alternative. It is the synthesis: **A's shell, B's inspector.**

A's graphite shell won because the map is the product and a dark, low-chroma
surround makes the network the brightest thing on screen. B's inspector won
because a paper panel is where you read numbers — 13 px small-caps labels on
`#ECEEF1` are legible in a way 9 px on graphite is not, and the ruled measure
rows scan vertically without boxes.

| Taken from A | Taken from B |
| --- | --- |
| Graphite map surface and dark top bar | Cool paper inspector `#ECEEF1` |
| Filled nav chip | Underlined direction tabs |
| Icon rail | Bordered scenario form cells |
| Map label and legend treatment | 24 px gutter, 3 px radius, ruled measure rows |
| | Sans hero, no monospace |

### Four corrections applied on top of the synthesis

1. **The hero is neutral, not red.** `--hero-ink: #12171D`, near-black charcoal.
   Red is the closure. Rendering `+53.5 km` in closure red made the *measurement*
   look like the alarm, and it pre-judged severity: 53.5 km is severe for a
   commuter and unremarkable for a freight operator with a schedule allowance.
   A and B alias `--hero-ink` to `--closure-ink`, so the token exists in all
   three and only C sets it independently — the difference is visible by
   toggling directions in the live source.
2. **No decorative severity rail.** A's red bar beside the hero encoded nothing;
   it was constant regardless of the value. Removed from C, retained in A so the
   comparison stays honest.
3. **`ESTIMATED` marked at the value, not in a footnote.** A small uppercase chip
   sits directly beside `56 min`, because that is the one figure on the panel not
   derived from measured geometry — AMDS publishes no speed attribute.
4. **Compact status.** `Represented-network alternative found` replaces the
   longer phrasing. It fits on one line beside the direction tag, and it says
   what was actually computed: a path through the network *as represented in this
   snapshot*, which is the honest scope.

### Monospace in C

Identifiers, versions, map labels and the selected comparison-table figures
only. The hero, the four measure values and all body copy are sans with
`tabular-nums`. `+53.5` at 58 px in monospace read as a terminal; in the sans it
reads as a headline, which is what it is.

### Known at 1280×800

The scenario controls (`CLOSURE` / `MEASURE` / `VEHICLE`) fall below the first
viewport on the laptop frame — `CLOSURE` is the last row visible. This is
deliberate rather than unnoticed: the answer, the four measures and the
forward/reverse comparison all clear the fold, and the controls are what you
scroll to when you want to change the question. Compressing the measure rows to
pull the controls up would cost the readability that is the reason for taking
B's inspector in the first place.

---

## How A and B differ

They share DOM, information architecture and data. They differ in treatment.

| | A — Graphite Command Centre | B — Hybrid Atlas |
| --- | --- | --- |
| Panel ground | Same graphite as the map | Cool paper `#ECEEF1` |
| Hero | 52 px monospace, red severity rail | 58 px sans, no rail |
| Measure rows | 9 px, sentence labels | 13 px, small-caps labels, 2 px top rule |
| Direction control | Segmented pills | Underlined text tabs |
| Scenario controls | Filled segments | Bordered form cells |
| Gutter | 18 px | 24 px |
| Corner radius | 6 px | 2 px |
| Route stroke | 3.2 px | 2.8 px, flatter |
| Nav | Filled chip | Underline |

Cool rather than warm paper in B is deliberate: cream with a serif is the
overused "old atlas" look.

### Monospace, used selectively

Reserved for the hero figure in A, the comparison-table numbers, identifiers,
versions and map labels. Secondary measures use the sans with `tabular-nums`,
so columns still align without the panel reading as a terminal. **B and C set
their hero in the sans entirely.**

The artifact CSP blocks webfonts, which is why these concepts use system
stacks. That is a constraint on the concept, not on the product: the React
application should bundle a licensed face locally.

### Cartography

Representative, not survey data. Local roads branch off the highway corridors so
the canvas reads as a connected network, and the replacement path follows those
corridors — which is what the real 55.45 km detour does. Generation is
deterministic so successive screenshots are identical.

The scale bar is the only measurement claim on the map, and it is honest. There
are no decorative chainage marks or grid references implying precision the
concept does not carry.

**Browser fidelity sign-off still needs a real LINZ key** in
`VITE_LINZ_API_KEY`. These concepts settle layout, hierarchy, typography and
colour; they do not settle how the product looks over real basemap tiles.

---

## Bugs the screenshots caught

Worth recording, because both were invisible in the source and only appeared
once the pixels were rendered:

1. **No `<!doctype html>`** put the page in quirks mode, where table cells stop
   inheriting colour — the entire forward/reverse comparison table rendered in
   near-black on near-black and was effectively invisible in direction A.
2. **`.body` had no `min-height: 0`**, so inspector content stretched the grid
   row and the map grew to 1,191 px. The legend, scale bar and attribution were
   pushed below the 900 px frame and never appeared.

None would have been caught by reading the markup.

A third, from the Direction C round, is a capture-harness fault rather than a
design one but has the same lesson: the first C render came back **blank below
996 px** because the browser viewport had reset to 996 px wide and the region
outside it was never painted, so the element screenshot captured empty pixels
rather than failing. Fixed by resizing to 1520×1000 before capture, and recorded
in the regeneration note above. The general point holds — an image that renders
is not the same as an image that is correct, so all three C frames were read
back and checked against the review criteria before commit.
