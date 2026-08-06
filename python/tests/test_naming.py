"""The naming rules, tested against the shapes that actually occur in AMDS.

Every fixture below is a real record from AMDS table 11, reduced to the fields
under test. Made-up inputs would only prove the code agrees with itself.
"""

from __future__ import annotations

import pytest

from nzcl.naming import (
    AMBIGUOUS_CONFLICT,
    AMDS_NAMED,
    EPOCH_MS_1900,
    EPOCH_MS_9999,
    ROUTE_DESIGNATION_ONLY,
    SOURCE_AMDS,
    UNRESOLVED,
    Candidate,
    RouteName,
    build_candidates,
    fold_ascii,
    format_designation,
    is_designation,
    parse_route_number,
    search_key,
    select_amds_name,
    starts_with_route_reference,
)

NOW = 1786000000000  # August 2026, in epoch milliseconds


def rn(**kw) -> RouteName:
    base = dict(
        route_name_id="{00000000-0000-0000-0000-000000000000}",
        status=1, effective_from=EPOCH_MS_1900, effective_to=EPOCH_MS_9999,
        group=6,
    )
    base.update(kw)
    return RouteName(**base)


# --------------------------------------------------------------------- text

def test_fold_ascii_preserves_nothing_but_strips_macrons():
    assert fold_ascii("Mangamōteo Street") == "Mangamoteo Street"
    assert fold_ascii("Mākakahi Street") == "Makakahi Street"
    assert fold_ascii("Te Ara Pūrātā") == "Te Ara Purata"
    assert fold_ascii(None) is None
    assert fold_ascii("   ") is None


def test_display_name_keeps_its_macrons():
    """The fold is a search key. It must never become the display name."""
    sel = select_amds_name([Candidate(rn(name_full="Mangamōteo Street"), True)],
                           now_ms=NOW)
    assert sel.display_name == "Mangamōteo Street"
    assert sel.native_name_key == "Mangamoteo Street"


@pytest.mark.parametrize("a,b", [
    ("Essex Street (Sh 1)", "ESSEX ST"),
    ("Queen Street", "queen  street"),
    ("Mangamōteo Street", "Mangamoteo St"),
    ("Great South Rd", "Great South Road"),
])
def test_search_key_matches_across_sources(a, b):
    assert search_key(a) == search_key(b)


def test_saint_is_not_street():
    """"St Andrews Road" and "Street Andrews Road" are not the same road."""
    assert search_key("St Andrews Road") == search_key("Saint Andrews Road")
    assert search_key("Andrews St") != search_key("Andrews Saint")


def test_different_roads_do_not_collide():
    assert search_key("Queen Street") != search_key("Queens Street")
    assert search_key("High Street") != search_key("High Road")


# -------------------------------------------------------------- designation

@pytest.mark.parametrize("name,group,expected", [
    # Pure route codes: nothing survives the reference but code words.
    ("SH 1S/774", 1, True),
    ("SH 2/883 INCREASING", 1, True),
    ("SH 1N/398 (11.65) DECREASING", 1, True),
    ("SH 1N/491 RAMP (SH) #1 ON", 1, True),
    ("State Highway #63 (Rs 0)", 6, True),
    ("SH 26/1 REVOKE", 6, True),
    ("State Highway 6 Highway (NZTA)", 1, True),
    ("Sh 2 North", 1, True),
    ("002-0161-R1", 6, True),               # RAMM-style section code
    ("01N-0475-R4", 6, True),
    # Real names that happen to open with a route reference.
    ("Sh 67 Buller Road", 6, False),
    ("Sh 2 Main North Road", 6, False),      # "north" is a code word, "road" is not
    ("Sh 1 Tinwald Service Lane", 6, False),
    ("Sh 1 Rs 416 Chertsey To Ashburton", 6, False),
    ("SH 8 [BEAUMONT BRIDGE]", 6, False),
    ("State Highway 3 Interchange 279 Roundabout", 1, False),
    # Names with no route reference at all.
    ("Essex Street (Sh 1)", 6, False),
    ("Shalimar Drive", 6, False),           # "SH" then no digit
    ("Shortland Street", 6, False),
])
def test_is_designation(name, group, expected):
    assert is_designation(name, group) is expected


@pytest.mark.parametrize("name,expected", [
    ("SH 16/47", 16),
    ("State Highway #63 (Rs 0)", 63),
    ("SH 1N/336", 1),
    ("002-0185/00.22", 2),
    ("01N-0475-R4", 1),
    ("57T-0050/14.347", 57),
    ("Sh 67 Buller Road", 67),
    ("Queen Street", None),
])
def test_parse_route_number(name, expected):
    assert parse_route_number(name) == expected


def test_route_reference_does_not_swallow_the_following_word():
    """"State Highway 6 Interchange 1" must not parse as highway "6I"."""
    assert parse_route_number("State Highway 6 Interchange 1 Roundabout") == 6
    assert is_designation("State Highway 6 Interchange 1 Roundabout", 1) is False


def test_format_designation_drops_the_island_suffix():
    """"1N"/"1S" split State Highway 1 for asset management. The public name
    of the road is State Highway 1 either way."""
    assert format_designation(1, "S") == "State Highway 1"
    assert format_designation(1, "N") == "State Highway 1"
    assert format_designation(3, None) == "State Highway 3"
    assert format_designation(None, None) is None


# ------------------------------------------------------------------ ranking

def test_street_name_wins_over_highway_designation():
    """The real case: 1,095 links carry both a group-6 street name and a
    group-1 highway string. "SH 1S/774" is not what to put on the label."""
    sel = select_amds_name([
        Candidate(rn(route_name_id="{b}", group=1, name_full="SH 1S/774",
                     route_number=1, route_alpha="S"), False),
        Candidate(rn(route_name_id="{a}", group=6,
                     name_full="Essex Street (Sh 1)"), False),
    ], now_ms=NOW)
    assert sel.display_name == "Essex Street (Sh 1)"
    assert sel.name_status == AMDS_NAMED
    assert sel.route_designation == "State Highway 1"
    assert sel.designation_raw == "SH 1S/774"
    assert not sel.conflict


def test_designation_only_link_shows_the_normalised_route():
    sel = select_amds_name([
        Candidate(rn(group=1, name_full="SH 1S/774", route_number=1,
                     route_alpha="S"), True),
    ], now_ms=NOW)
    assert sel.name_status == ROUTE_DESIGNATION_ONLY
    assert sel.display_name == "State Highway 1"
    assert sel.designation_raw == "SH 1S/774"
    assert sel.source_field == "routeNumber1"


def test_section_code_is_read_for_its_highway_number():
    """337 group-6 records carry only a code like "01N-0475-R4". AMDS supplies
    no routeNumber1 for them, but the leading digits are the highway."""
    sel = select_amds_name([
        Candidate(rn(group=6, name_full="01N-0475-R4", route_number=None), True),
    ], now_ms=NOW)
    assert sel.name_status == ROUTE_DESIGNATION_ONLY
    assert sel.display_name == "State Highway 1"
    assert sel.designation_raw == "01N-0475-R4"


def test_a_road_named_after_its_highway_keeps_its_name():
    """"Sh 67 Buller Road" is Buller Road. Replacing it with "State Highway 67"
    would throw away the only street name the link has."""
    sel = select_amds_name([
        Candidate(rn(group=6, name_full="Sh 67 Buller Road"), True),
    ], now_ms=NOW)
    assert sel.display_name == "Sh 67 Buller Road"
    assert sel.name_status == AMDS_NAMED
    assert sel.route_designation == "State Highway 67"


def test_a_plain_street_name_beats_one_that_opens_with_a_route_reference():
    sel = select_amds_name([
        Candidate(rn(route_name_id="{z}", name_full="Sh 1 Tinwald Service Lane"),
                  False),
        Candidate(rn(route_name_id="{a}", name_full="Archibald Road"), False),
    ], now_ms=NOW)
    assert sel.display_name == "Archibald Road"
    assert sel.conflict is False   # different kinds are not a disagreement
    assert starts_with_route_reference("Sh 1 Tinwald Service Lane")


def test_primary_flag_is_respected_within_a_kind():
    sel = select_amds_name([
        Candidate(rn(route_name_id="{a}", name_full="Von Road"), False),
        Candidate(rn(route_name_id="{z}", name_full="Mount Nicholas Road"), True),
    ], now_ms=NOW)
    assert sel.display_name == "Mount Nicholas Road"


def test_selection_is_deterministic_regardless_of_input_order():
    """The defect this replaces: whichever join row arrived first won. 194
    links carry more than one primary flag, so order decided the name."""
    a = Candidate(rn(route_name_id="{4d2a7b26}",
                     name_full="Mount Nicholas Road (Te Anau Ward)"), True)
    b = Candidate(rn(route_name_id="{fdedab37}", name_full="Von Road"), True)
    assert (select_amds_name([a, b], now_ms=NOW).display_name
            == select_amds_name([b, a], now_ms=NOW).display_name)


def test_two_different_roadway_names_are_a_conflict_not_a_silent_pick():
    sel = select_amds_name([
        Candidate(rn(route_name_id="{4d2a7b26}",
                     name_full="Mount Nicholas Road (Te Anau Ward)"), True),
        Candidate(rn(route_name_id="{fdedab37}", name_full="Von Road"), True),
    ], now_ms=NOW)
    assert sel.name_status == AMBIGUOUS_CONFLICT
    assert sel.conflict is True
    assert "Von Road" in sel.alternates
    assert "MULTIPLE_PRIMARY_FLAGS" in sel.notes


def test_street_name_plus_its_own_designation_is_not_a_conflict():
    sel = select_amds_name([
        Candidate(rn(route_name_id="{a}", name_full="Queen Street"), False),
        Candidate(rn(route_name_id="{b}", group=1,
                     name_full="SH 2/883 INCREASING", route_number=2), False),
    ], now_ms=NOW)
    assert sel.name_status == AMDS_NAMED
    assert sel.conflict is False
    assert "NO_PRIMARY_FLAG_IN_SOURCE" in sel.notes


def test_qualified_and_unqualified_forms_of_one_name_are_not_a_conflict():
    """"Terrace Road" and "Terrace Road (Gdc Rd Te Tipua Ward)" are the same
    road with an administrative qualifier."""
    sel = select_amds_name([
        Candidate(rn(route_name_id="{a}", name_full="Terrace Road"), True),
        Candidate(rn(route_name_id="{b}",
                     name_full="Terrace Road (Gdc Rd Te Tipua Ward)"), True),
    ], now_ms=NOW)
    assert sel.conflict is False
    assert sel.name_status == AMDS_NAMED


# ------------------------------------------------------------- status/dates

def test_retired_names_are_not_displayed():
    sel = select_amds_name([
        Candidate(rn(name_full="Holliss Avenue", status=2), True),
    ], now_ms=NOW)
    assert sel.name_status == UNRESOLVED
    assert sel.display_name is None
    assert "ALL_AMDS_NAMES_EXPIRED_OR_RETIRED" in sel.notes


def test_expired_names_are_not_displayed():
    """Nine live-status names have an effectiveTo in the past."""
    sel = select_amds_name([
        Candidate(rn(name_full="Conlon Street", status=1,
                     effective_to=1749000000000), True),  # June 2025
    ], now_ms=NOW)
    assert sel.name_status == UNRESOLVED


def test_an_expired_name_does_not_beat_a_live_one():
    sel = select_amds_name([
        Candidate(rn(route_name_id="{a}", name_full="Conlon Street",
                     effective_to=1749000000000), True),
        Candidate(rn(route_name_id="{b}", name_full="Cavendish Drive"), False),
    ], now_ms=NOW)
    assert sel.display_name == "Cavendish Drive"


def test_far_future_sentinel_is_treated_as_open_ended():
    sel = select_amds_name([
        Candidate(rn(name_full="Crown Range", effective_to=EPOCH_MS_9999), True),
    ], now_ms=NOW)
    assert sel.display_name == "Crown Range"


def test_no_candidates_at_all_is_unresolved_without_a_note():
    sel = select_amds_name([], now_ms=NOW)
    assert sel.name_status == UNRESOLVED
    assert sel.notes == ()


# ---------------------------------------------------------------- provenance

def test_provenance_is_retained():
    sel = select_amds_name([
        Candidate(rn(route_name_id="{a}", name_full="Queen Street",
                     locality_code=6, effective_from=EPOCH_MS_1900), True),
        Candidate(rn(route_name_id="{b}", group=1, name_full="SH 2/883",
                     route_number=2), False),
    ], now_ms=NOW)
    assert sel.name_source == SOURCE_AMDS
    assert sel.source_field == "routeNameFull"
    assert set(sel.route_name_ids) == {"{a}", "{b}"}
    assert sel.primary_route_name_id == "{a}"
    assert sel.alternates == ("SH 2/883",)
    assert sel.locality_code == 6
    assert sel.effective_from == EPOCH_MS_1900


def test_ramp_records_are_marked():
    sel = select_amds_name([
        Candidate(rn(name_full="Kotare Lane", ramp_number=4), True),
    ], now_ms=NOW)
    assert sel.is_ramp is True


def test_published_ascii_column_is_never_the_display_name():
    """One record reads "Kotare Lane" in routeNameFull and "SH 1N/458 RAMP
    (SH) #4 OFF" in routeNameFullASCII. Its structured components say Kotare
    Lane. The ASCII column is not a transliteration and is not trusted."""
    sel = select_amds_name([
        Candidate(rn(name_full="Kotare Lane",
                     name_ascii_published="SH 1N/458 RAMP (SH) #4 OFF"), True),
    ], now_ms=NOW)
    assert sel.display_name == "Kotare Lane"
    assert sel.native_name_key == "Kotare Lane"


# ------------------------------------------------------------------ grouping

def test_build_candidates_drops_join_rows_with_no_detail():
    cands = build_candidates(
        join_rows=[
            {"amdsIDNetworkModel": "{L1}", "amdsIDRouteName": "{R1}", "isPrimary": 1},
            {"amdsIDNetworkModel": "{L1}", "amdsIDRouteName": "{missing}",
             "isPrimary": 0},
        ],
        route_name_rows=[
            {"amdsIDRouteName": "{R1}", "routeNameFull": "Queen Street",
             "routeGroup": 6, "status": 1},
        ],
    )
    assert list(cands) == ["{L1}"]
    assert len(cands["{L1}"]) == 1
    assert cands["{L1}"][0].is_primary is True
