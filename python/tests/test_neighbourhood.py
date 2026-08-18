"""A bounded copy must prove itself before anything is believed from it.

The hazard these tests exist for: a neighbourhood copy CUTS the network at its
boundary, so a replacement route that would have left and come back finds the
edge missing and reports a longer route - or DISCONNECTED - that is an
artefact of the copy. A counterfactual reporting a number produced that way is
worse than one that declines, because it is wrong in a direction nobody can
see.

So the copy is made to reproduce the canonical answer with NOTHING assumed
before any counterfactual is run against it, and if no admissible radius does,
the result is UNRESOLVED and says so.
"""

from __future__ import annotations

import pytest

from nzcl import neighbourhood, whatif
from nzcl.neighbourhood import (MAX_LINKS, Extraction, NeighbourhoodTooSmall,
                                extract_validated)
from nzcl.sensitivity import Answer

pytestmark = pytest.mark.usefixtures("synthetic")


CANON = Answer(status="OK", distance_m=7944.4, is_bridge=False,
               isolated_link_count=0)
FAR = Answer(status="DISCONNECTED", distance_m=None, is_bridge=True,
             isolated_link_count=9)


def fake_extract(monkeypatch, by_radius: dict[float, int]):
    """Record which radii were tried, without touching a database."""
    made: list[str] = []
    dropped: list[str] = []

    def _extract(src, link_id, *, radius_m, dst=None):
        n = by_radius[radius_m]
        if n > MAX_LINKS:
            raise NeighbourhoodTooSmall(
                (radius_m,), (n,),
                f"a {radius_m:.0f} m neighbourhood holds {n} links, over the "
                f"{MAX_LINKS} ceiling")
        sid = f"cf-{radius_m:.0f}"
        made.append(sid)
        return Extraction(snapshot_id=sid, source_snapshot_id=src,
                          radius_m=radius_m, link_count=n,
                          node_count=n * 2, seconds=0.01)

    monkeypatch.setattr(neighbourhood, "extract", _extract)
    monkeypatch.setattr(whatif, "drop_snapshot", lambda s: dropped.append(s))
    return made, dropped


class TestTheCopyMustReproduceTheCanonicalAnswer:
    def test_the_smallest_radius_that_agrees_is_used(self, monkeypatch):
        made, dropped = fake_extract(monkeypatch,
                                     {5000.0: 400, 12000.0: 900, 30000.0: 4000})
        ex = extract_validated("snap", 1, canonical=CANON,
                               answer_of=lambda sid: CANON)
        assert ex.radius_m == 5000.0
        assert ex.validated is True
        assert made == ["cf-5000"] and dropped == []

    def test_a_boundary_effect_grows_the_radius(self, monkeypatch):
        made, dropped = fake_extract(monkeypatch,
                                     {5000.0: 400, 12000.0: 900, 30000.0: 4000})
        answers = {"cf-5000": FAR, "cf-12000": CANON}
        ex = extract_validated("snap", 1, canonical=CANON,
                               answer_of=lambda sid: answers[sid])
        assert ex.radius_m == 12000.0
        assert ex.validated is True

    def test_a_copy_that_did_not_agree_is_dropped_not_kept(self, monkeypatch):
        """Otherwise a later caller could reach for it."""
        made, dropped = fake_extract(monkeypatch,
                                     {5000.0: 400, 12000.0: 900, 30000.0: 4000})
        answers = {"cf-5000": FAR, "cf-12000": CANON}
        extract_validated("snap", 1, canonical=CANON,
                          answer_of=lambda sid: answers[sid])
        assert dropped == ["cf-5000"]

    def test_a_near_miss_on_distance_is_still_a_disagreement(self, monkeypatch):
        fake_extract(monkeypatch, {5000.0: 400, 12000.0: 900, 30000.0: 4000})
        nearly = Answer(status="OK", distance_m=7944.9, is_bridge=False,
                        isolated_link_count=0)
        answers = {"cf-5000": nearly, "cf-12000": CANON}
        ex = extract_validated("snap", 1, canonical=CANON,
                               answer_of=lambda sid: answers[sid])
        assert ex.radius_m == 12000.0


class TestItDeclinesRatherThanTruncating:
    """The correction that matters most."""

    def test_no_agreeing_radius_raises_rather_than_returning_the_biggest(
            self, monkeypatch):
        made, dropped = fake_extract(monkeypatch,
                                     {5000.0: 400, 12000.0: 900, 30000.0: 4000})
        with pytest.raises(NeighbourhoodTooSmall):
            extract_validated("snap", 1, canonical=CANON,
                              answer_of=lambda sid: FAR)

    def test_every_failed_copy_is_dropped(self, monkeypatch):
        made, dropped = fake_extract(monkeypatch,
                                     {5000.0: 400, 12000.0: 900, 30000.0: 4000})
        with pytest.raises(NeighbourhoodTooSmall):
            extract_validated("snap", 1, canonical=CANON,
                              answer_of=lambda sid: FAR)
        assert dropped == made == ["cf-5000", "cf-12000", "cf-30000"]

    def test_the_error_says_what_was_tried(self, monkeypatch):
        fake_extract(monkeypatch, {5000.0: 400, 12000.0: 900, 30000.0: 4000})
        with pytest.raises(NeighbourhoodTooSmall) as e:
            extract_validated("snap", 1, canonical=CANON,
                              answer_of=lambda sid: FAR)
        assert e.value.radii_tried == (5000.0, 12000.0, 30000.0)
        assert e.value.link_counts == (400, 900, 4000)
        assert "UNRESOLVED" in str(e.value)

    def test_the_link_ceiling_stops_the_search(self, monkeypatch):
        """A larger radius can only hold more links, so there is no point
        trying one after the ceiling has been hit."""
        made, _ = fake_extract(monkeypatch,
                               {5000.0: 400, 12000.0: MAX_LINKS + 1,
                                30000.0: 90_000})
        with pytest.raises(NeighbourhoodTooSmall) as e:
            extract_validated("snap", 1, canonical=CANON,
                              answer_of=lambda sid: FAR)
        assert made == ["cf-5000"]
        assert e.value.radii_tried == (5000.0, 12000.0)
        assert "ceiling" in str(e.value)

    def test_a_neighbourhood_over_the_ceiling_is_never_copied(self, monkeypatch):
        made, _ = fake_extract(monkeypatch, {5000.0: MAX_LINKS + 1,
                                             12000.0: 99_999,
                                             30000.0: 99_999})
        with pytest.raises(NeighbourhoodTooSmall):
            extract_validated("snap", 1, canonical=CANON,
                              answer_of=lambda sid: CANON)
        assert made == []


class TestTheBoundIsRecorded:
    def test_the_extraction_reports_its_radius_and_size(self):
        ex = Extraction(snapshot_id="cf-1", source_snapshot_id="snap",
                        radius_m=5000.0, link_count=412, node_count=800,
                        seconds=0.42, validated=True)
        d = ex.as_dict()
        assert d["radiusM"] == 5000.0
        assert d["linkCount"] == 412
        assert d["maxLinks"] == MAX_LINKS
        assert d["validatedAgainstCanonical"] is True

    def test_an_unvalidated_extraction_says_so(self):
        ex = Extraction(snapshot_id="cf-1", source_snapshot_id="snap",
                        radius_m=5000.0, link_count=412, node_count=800,
                        seconds=0.42)
        assert ex.as_dict()["validatedAgainstCanonical"] is False


@pytest.mark.realdata
class TestAgainstARealSnapshot:
    """Needs an ingested snapshot; deselected from the default suite."""

    def test_a_neighbourhood_is_a_small_fraction_of_the_network(self):
        from nzcl import db
        snaps = db.query("SELECT snapshot_id FROM network_snapshots "
                         " ORDER BY created_at DESC LIMIT 1")
        if not snaps:
            pytest.skip("no snapshot ingested")
        snap = snaps[0]["snapshot_id"]
        total = db.query("SELECT count(*) AS n FROM links WHERE snapshot_id=%s",
                         (snap,))[0]["n"]
        link = db.query("SELECT link_id FROM links WHERE snapshot_id=%s "
                        " LIMIT 1", (snap,))[0]["link_id"]
        near = neighbourhood.count_links_within(snap, link, 5000.0)
        assert near < total
