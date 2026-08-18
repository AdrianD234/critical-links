# Crossing review

You are looking at 440 aerial photographs of places in New Zealand where two
road centrelines cross each other. Each image is one place. For each one,
decide what is on the ground.

## What is drawn on each image

- the aerial imagery, north up, with a scale bar;
- **centreline A** in pink and **centreline B** in cyan - these are the two
  mapped road centrelines that cross here;
- a **yellow ring** around the exact point where the two centrelines cross;
- a card id at the top left.

Nothing else is drawn, and nothing about the two roads other than their
geometry is available to you. That is deliberate.

## The question

**At the yellow ring, can a vehicle travel from one road onto the other?**

Answer with exactly one of:

- `a` - **at grade**. The two roads meet on the ground and a vehicle can pass
  between them. An ordinary intersection, crossroads, T-junction under the
  ring, staggered junction, or a roundabout the ring sits on.
- `g` - **grade separated**. Both roads are there, but one passes over or
  under the other. A bridge, an overbridge, an underpass, a flyover, a tunnel.
  A vehicle cannot pass between them at this point.
- `n` - **not a junction**. There is no place here where two roads meet. Use
  this when what you see is one single road that has been drawn twice (both
  centrelines run along the same piece of tarmac), when one of the
  centrelines does not correspond to any visible road, or when the two lines
  cross somewhere no road exists at all.
- `u` - **unclear**. You cannot tell from this image. Cloud, shadow, tree
  canopy, the imagery is too old or too coarse, or the centrelines plainly do
  not line up with anything you can see.

Use `u` honestly. It is recorded as its own outcome and it is not a
throwaway. Do NOT guess in order to avoid it, and do NOT use it for a card
that is merely unusual - only for one you genuinely cannot read.

## How to decide between them

- **at grade vs grade separated**: look for a structure. Parapets or bridge
  rails, an abrupt change in the shape of the road edge, an embankment or
  cutting, one road's shadow falling on the other, ramps leaving and rejoining.
  If the two carriageways clearly touch and there is a visible connection -
  corner radii, a painted intersection, worn turning paths - it is at grade.
- **at grade vs not a junction**: ask whether there are TWO roads. If both
  coloured lines run along the same single strip of tarmac, or one line
  wanders across paddocks, buildings or water with no road under it, that is
  `n`, not `a`.
- A junction slightly offset from the ring is still `a` if the two roads
  connect at that place. The ring marks where the two lines cross, which is
  not always exactly where the tarmac meets.

## Output

Return one line per card, in card order:

    T001 a
    T002 g
    T003 u

If you want to record a reason, put it after the letter:

    T004 n  both lines run down the same farm track

Review every card. Do not skip any. If you are unsure, that is what `u` is
for.
