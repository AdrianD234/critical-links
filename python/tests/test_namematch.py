"""The matcher's decisions, on the shapes that actually caused wrong answers.

Every fixture is derived from a case the proof-of-concept run surfaced. The
first version of this matcher scored 94.0% two-source agreement on its
high-confidence class; these are the cases that took it to 99.24%, so they are
the ones that must not regress.
"""

from __future__ import annotations

import pytest

from nzcl.namematch import (
    HIGH,
    LOW,
    MEDIUM,
    NONE,
    classify,
    evaluate_group,
    is_ramp_name,
    normalise_external_name,
    score_all,
)

TOL = 12.0


def row(**kw):
    """A candidate row as the scoring query returns it."""
    base = dict(
        gid="{G}", source="linz_road_sections", feature_id="1", part=0,
        display_name="Queen Street", name_key="queen street",
        is_unnamed=None, is_state_highway=None, is_dual_carriageway=None,
        cand_oneway=None, cand_status=None, locality=None, locality_alt=None,
        territorial_authority=None, territorial_authority_alt=None,
        linz_road_section_ids=None, corridor=None, route_code=None, extra={},
        rca_code=None, rca_name=None, target_oneway=None, urban_rural=None,
        link_count=1, split_parent=False,
        target_len=500.0, cand_len=500.0,
        sep=[0.5] * 21, cand_covered_frac=1.0,
        start_dist=0.4, end_dist=0.4, target_az=90.0, cand_az=90.0,
    )
    base.update(kw)
    return base


def far(distance: float, n: int = 21):
    return [distance] * n


# ------------------------------------------------------------------ merging

def test_fragments_of_one_name_are_merged_before_scoring():
    """A 5 km rural road is eight LINZ sections. Scored one at a time each
    covers an eighth of it and nothing ever reaches high confidence."""
    halves = [
        row(feature_id="a", sep=[0.5] * 11 + [900.0] * 10),
        row(feature_id="b", sep=[900.0] * 11 + [0.5] * 10),
    ]
    merged = evaluate_group(halves, tolerance_m=TOL)
    assert merged.covered_frac == 1.0
    assert merged.parts == 2
    # Neither fragment could have got there alone.
    assert evaluate_group([halves[0]], tolerance_m=TOL).covered_frac < 0.6


def test_merging_is_by_source_and_name_not_across_them():
    groups = score_all([
        row(source="linz_road_sections", name_key="queen street"),
        row(source="nzta_street_names", name_key="queen street",
            display_name="QUEEN STREET"),
        row(source="linz_road_sections", name_key="king street",
            display_name="King Street", sep=far(30.0)),
    ])
    assert len(groups) == 3


# ------------------------------------------------------------------- rivals

def test_a_close_rival_with_a_different_name_blocks_high_confidence():
    """The dual-carriageway and frontage-road case. Two plausible roads with
    different names is not resolved by taking the nearer one."""
    out = classify("{G}", [
        row(feature_id="a", display_name="Queen Street", name_key="queen street"),
        row(feature_id="b", display_name="King Street", name_key="king street",
            sep=[1.0] * 21),
    ])
    assert out.confidence == MEDIUM
    assert "RIVAL_NAME_TOO_CLOSE" in out.reasons
    assert out.rival_name in {"Queen Street", "King Street"}


def test_a_clearly_worse_rival_does_not_block():
    out = classify("{G}", [
        row(feature_id="a", display_name="Queen Street", name_key="queen street"),
        row(feature_id="b", display_name="King Street", name_key="king street",
            sep=far(35.0), cand_covered_frac=0.1, cand_az=10.0),
    ])
    assert out.confidence == HIGH
    assert out.name == "Queen Street"


def test_the_same_name_from_two_sources_is_not_a_rival():
    out = classify("{G}", [
        row(source="linz_road_sections", display_name="Queen Street",
            name_key="queen street"),
        row(source="nzta_street_names", display_name="QUEEN STREET",
            name_key="queen street"),
    ])
    assert out.confidence == HIGH
    assert out.rival_name is None


# ------------------------------------------------------- designation vs name

def test_a_highway_designation_does_not_compete_with_a_street_name():
    """The single biggest source of false disagreements: NZTA calls it
    "GREAT SOUTH ROAD" and LINZ calls the same alignment "State Highway 1".
    The road has a name and carries a route; both are true."""
    out = classify("{G}", [
        row(source="nzta_street_names", display_name="GREAT SOUTH ROAD",
            name_key="great south road"),
        row(source="linz_road_sections", display_name="State Highway 1",
            name_key="state highway 1"),
    ])
    assert out.confidence == HIGH
    assert out.name == "GREAT SOUTH ROAD"
    assert out.designation == "State Highway 1"
    assert "RIVAL_NAME_TOO_CLOSE" not in out.reasons


def test_two_different_designations_are_still_a_disagreement():
    """State Highway 1 against State Highway 59 is a real conflict - the
    Paekakariki section was renumbered and one source is stale."""
    out = classify("{G}", [
        row(source="linz_road_sections", display_name="State Highway 59",
            name_key="state highway 59"),
        row(source="nzta_street_names", display_name="SH 1",
            name_key="state highway 1"),
    ])
    assert out.confidence == MEDIUM
    assert "RIVAL_NAME_TOO_CLOSE" in out.reasons


def test_a_designation_only_match_is_marked_as_such():
    out = classify("{G}", [
        row(display_name="State Highway 3", name_key="state highway 3"),
    ])
    assert "DESIGNATION_ONLY" in out.reasons
    assert out.name == "State Highway 3"


# -------------------------------------------------------------------- ramps

@pytest.mark.parametrize("name,expected", [
    ("QUARRY ROAD OFF RAMP", True),
    ("LAMBIE DRIVE ON RAMP", True),
    ("Koheroa Road On/Off Ramp", True),
    ("State Highway 1 Interchange 860", True),
    ("Quarry Road", False),
    ("Rampart Street", False),      # "ramp" must not match inside a word
])
def test_is_ramp_name(name, expected):
    assert is_ramp_name(name) is expected


def test_a_ramp_is_never_adopted_automatically():
    """A ramp runs alongside the mainline for its whole length, so geometry
    alone cannot separate them however good the numbers look."""
    out = classify("{G}", [
        row(display_name="QUARRY ROAD OFF RAMP", name_key="quarry road off ramp"),
    ])
    assert out.confidence == MEDIUM
    assert "RAMP_NOT_AUTO_ADOPTED" in out.reasons


def test_a_ramp_named_by_one_source_downgrades_the_whole_feature():
    out = classify("{G}", [
        row(source="linz_road_sections", display_name="Tecoma Street",
            name_key="tecoma street"),
        row(source="nzta_street_names", display_name="TECOMA STREET OFF RAMP",
            name_key="tecoma street off ramp", sep=far(30.0),
            cand_covered_frac=0.2),
    ])
    assert out.confidence != HIGH
    assert "RAMP_NOT_AUTO_ADOPTED" in out.reasons


def test_a_divided_carriageway_must_be_fully_covered():
    out = classify("{G}", [
        row(display_name="Great North Road", name_key="great north road",
            is_dual_carriageway=True, sep=[0.5] * 19 + [15.0, 15.0]),
    ])
    assert out.confidence == MEDIUM
    assert "DIVIDED_CARRIAGEWAY_NEEDS_FULL_COVER" in out.reasons


# --------------------------------------------------------- officially unnamed

def test_an_explicit_unnamed_classification_is_a_resolved_answer():
    out = classify("{G}", [
        row(source="nzta_street_names", display_name=None, name_key=None,
            is_unnamed=True),
    ])
    assert out.confidence == HIGH
    assert out.officially_unnamed is True
    assert out.name is None
    assert "SOURCE_MARKS_ROAD_UNNAMED" in out.reasons


def test_only_the_source_that_publishes_the_flag_can_set_it():
    """LINZ road sections is an addressing layer: it carries roads that HAVE
    names, so its silence is not a statement that a road has none."""
    out = classify("{G}", [
        row(source="linz_road_sections", display_name=None, name_key=None,
            is_unnamed=None),
    ])
    assert out.officially_unnamed is False
    assert out.confidence == NONE


def test_a_distant_unnamed_feature_does_not_classify_anything():
    out = classify("{G}", [
        row(source="nzta_street_names", display_name=None, name_key=None,
            is_unnamed=True, sep=far(120.0), cand_covered_frac=0.0),
    ])
    assert out.officially_unnamed is False


# -------------------------------------------------------------- attribution

def test_an_equally_good_cleared_source_gets_the_credit():
    """Attribution moves; the name does not. Used so a name both sources agree
    on can be displayed under the licence that permits it."""
    out = classify("{G}", [
        row(source="nzta_street_names", display_name="QUEEN STREET",
            name_key="queen street", sep=[0.2] * 21),
        row(source="linz_road_sections", display_name="Queen Street",
            name_key="queen street", sep=[0.6] * 21),
    ], preferred_sources=["linz_road_sections"])
    assert out.source == "linz_road_sections"
    assert out.name == "Queen Street"
    assert "ATTRIBUTED_TO_PREFERRED_SOURCE" in out.reasons


def test_a_preferred_source_is_never_promoted_to_a_name_it_did_not_earn():
    out = classify("{G}", [
        row(source="nzta_street_names", display_name="QUEEN STREET",
            name_key="queen street"),
        row(source="linz_road_sections", display_name="Queen Street",
            name_key="queen street", sep=far(40.0), cand_covered_frac=0.0),
    ], preferred_sources=["linz_road_sections"])
    assert out.source == "nzta_street_names"
    assert "ATTRIBUTED_TO_PREFERRED_SOURCE" not in out.reasons


# ------------------------------------------------------------------ general

def test_no_candidates_is_reported_not_guessed():
    out = classify("{G}", [])
    assert out.confidence == NONE
    assert out.name is None
    assert out.reasons == ("NO_CANDIDATE",)


def test_a_distant_candidate_does_not_reach_high_confidence():
    out = classify("{G}", [
        row(sep=far(25.0), cand_covered_frac=0.2)])
    assert out.confidence in (LOW, NONE)


def test_every_candidate_is_kept_not_only_the_winner():
    out = classify("{G}", [
        row(feature_id="a", display_name="Queen Street", name_key="queen street"),
        row(feature_id="b", display_name="King Street", name_key="king street",
            sep=far(30.0)),
        row(feature_id="c", display_name="Bank Street", name_key="bank street",
            sep=far(38.0)),
    ])
    assert len(out.candidates) == 3
    assert all(c.evidence()["score"] is not None for c in out.candidates)


@pytest.mark.parametrize("raw,expected", [
    ("SH 3", "State Highway 3"),
    ("State Highway 3", "State Highway 3"),
    ("Queen Street", "Queen Street"),
    (None, None),
])
def test_external_names_are_normalised_the_same_way_as_amds_ones(raw, expected):
    assert normalise_external_name(raw) == expected
