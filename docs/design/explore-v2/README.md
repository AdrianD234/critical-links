# Explore — design checkpoint 1

Two visual directions for the Explore screen, for selection before the frontend
is rebuilt.

| File | What it shows |
| --- | --- |
| `explore-concepts.html` | Live source. Toggles direction (A/B) and viewport (1440/1280). |
| `graphite-1440x900.png` | Direction A, desktop |
| `hybrid-atlas-1440x900.png` | Direction B, desktop |
| `graphite-1280x800.png` | Direction A, laptop |
| `hybrid-atlas-1280x800.png` | Direction B, laptop |
| `graphite-inspector-detail.png` | Direction A inspector, 1:1 |
| `hybrid-atlas-inspector-detail.png` | Direction B inspector, 1:1 |

Regenerate: serve this directory over HTTP and open
`?dir=a|b&size=desktop|laptop&bare=1`. `bare=1` strips the page chrome so the
1440×900 frame can be captured on its own.

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
and puts the result below the fold. Both concepts invert that:

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

## How the two directions differ

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
so columns still align without the panel reading as a terminal. **B sets its
hero in the sans entirely.**

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

Neither would have been caught by reading the markup.
