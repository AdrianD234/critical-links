"""A counterfactual copy must never become the application's road network.

THE DEFECT THIS GUARDS
----------------------
A bounded counterfactual copy is inserted into `network_snapshots` like any
other snapshot, and it inherits `status='complete'` and
`coverage_kind='national'` from its source. `api.snapshot_id()` selects the
newest complete national snapshot. So while such a copy existed, an 800-link
fragment covering 5 km of one district would win on recency and become the
network served to users - and every closure outside the fragment would report
DISCONNECTED.

That would be a considerably worse defect than anything this branch has fixed,
and it would appear and vanish depending on whether somebody happened to be
running an analysis.

Cancellation is not an exotic path here either: the sensitivity endpoint is
meant to be cancellable, so abandoning an analysis is the expected case rather
than the unlucky one.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import pathlib
import re

import pytest

from nzcl import neighbourhood, whatif
from nzcl.neighbourhood import Extraction, NeighbourhoodTooSmall, borrowed
from nzcl.sensitivity import Answer

CANON = Answer(status="OK", distance_m=7944.4, is_bridge=False,
               isolated_link_count=0)
FAR = Answer(status="DISCONNECTED", distance_m=None, is_bridge=True,
             isolated_link_count=9)

SRC = pathlib.Path(neighbourhood.__file__).parent


def _source(module: str) -> str:
    return (SRC / f"{module}.py").read_text(encoding="utf-8")


class TestNoAutomaticSelectionCanReachATransientCopy:
    """Read the source. A runtime test only covers the paths it happens to
    run, and the risk here is a query nobody thought to exercise."""

    def test_every_choosing_query_in_the_api_excludes_transient(self):
        src = _source("api")
        blocks = re.findall(
            r"FROM network_snapshots[\s\S]{0,300}?ORDER BY[^\"]*", src)
        assert blocks, "the selection queries moved; this guard must follow"
        for q in blocks:
            assert "is_transient" in q, q

    @pytest.mark.parametrize("module", ["batch", "bench", "benchv2",
                                        "crossvalidate"])
    def test_the_other_entry_points_exclude_it_too(self, module):
        src = _source(module)
        if "FROM network_snapshots" not in src:
            pytest.skip(f"{module} does not select a snapshot")
        blocks = re.findall(
            r"FROM network_snapshots[\s\S]{0,300}?ORDER BY[^\"]*", src)
        for q in blocks:
            assert "is_transient" in q, f"{module}: {q}"

    def test_the_extractor_sets_both_independent_guards(self):
        """Either alone is one edit away from being undone."""
        src = _source("neighbourhood")
        assert 'row["is_transient"] = True' in src
        assert '"counterfactual"' in src

    def test_the_migration_adds_the_column_and_back_marks_old_copies(self):
        repo = SRC.parents[2]          # python/src/nzcl -> repo root
        text = (repo / "sql/migrations/010_transient_snapshots.sql").read_text(
            encoding="utf-8")
        assert "is_transient boolean NOT NULL DEFAULT false" in text
        assert "UPDATE network_snapshots" in text and "cf-%" in text
        assert "transient_created_at" in text

    def test_the_docstring_says_why_rather_than_just_what(self):
        assert "TRANSIENT" in _source("api")


def _borrowable(monkeypatch, snapshot_id="cf-borrowed"):
    dropped: list[str] = []
    monkeypatch.setattr(
        neighbourhood, "extract_validated",
        lambda src, link_id, **kw: Extraction(
            snapshot_id=snapshot_id, source_snapshot_id=src, radius_m=5000.0,
            link_count=10, node_count=20, seconds=0.01, validated=True))
    monkeypatch.setattr(whatif, "drop_snapshot", lambda s: dropped.append(s))
    return dropped


class TestTheCopyIsAlwaysDropped:
    def test_a_normal_block_drops_it(self, monkeypatch):
        dropped = _borrowable(monkeypatch)
        with borrowed("snap", 1, canonical=CANON,
                      answer_of=lambda s: CANON) as nb:
            assert nb.snapshot_id == "cf-borrowed"
            assert dropped == []
        assert dropped == ["cf-borrowed"]

    def test_an_exception_inside_the_block_still_drops_it(self, monkeypatch):
        dropped = _borrowable(monkeypatch)
        with pytest.raises(RuntimeError):
            with borrowed("snap", 1, canonical=CANON,
                          answer_of=lambda s: CANON):
                raise RuntimeError("routing blew up")
        assert dropped == ["cf-borrowed"]

    def test_cancellation_still_drops_it(self, monkeypatch):
        dropped = _borrowable(monkeypatch)
        with pytest.raises(concurrent.futures.CancelledError):
            with borrowed("snap", 1, canonical=CANON,
                          answer_of=lambda s: CANON):
                raise concurrent.futures.CancelledError()
        assert dropped == ["cf-borrowed"]

    def test_a_keyboard_interrupt_still_drops_it(self, monkeypatch):
        """BaseException, not Exception - a bare `except Exception` here would
        leave the copy behind on the one path most likely to happen."""
        dropped = _borrowable(monkeypatch)
        with pytest.raises(KeyboardInterrupt):
            with borrowed("snap", 1, canonical=CANON,
                          answer_of=lambda s: CANON):
                raise KeyboardInterrupt()
        assert dropped == ["cf-borrowed"]

    def test_a_failed_extraction_never_enters_the_block(self, monkeypatch):
        def _fail(src, link_id, **kw):
            raise NeighbourhoodTooSmall((5000.0,), (400,), "boundary bit")

        monkeypatch.setattr(neighbourhood, "extract_validated", _fail)
        with pytest.raises(NeighbourhoodTooSmall):
            with borrowed("snap", 1, canonical=CANON,
                          answer_of=lambda s: FAR):
                pytest.fail("the block must not run")


class TestConcurrentAnalysesDoNotCollide:
    def test_each_analysis_gets_its_own_id(self, monkeypatch):
        seen: list[str] = []

        def _extract(src, link_id, *, radius_m, dst=None):
            import uuid
            sid = dst or f"cf-{uuid.uuid4().hex[:12]}"
            seen.append(sid)
            return Extraction(snapshot_id=sid, source_snapshot_id=src,
                              radius_m=radius_m, link_count=10, node_count=20,
                              seconds=0.01)

        monkeypatch.setattr(neighbourhood, "extract", _extract)
        monkeypatch.setattr(whatif, "drop_snapshot", lambda s: None)
        for _ in range(8):
            neighbourhood.extract_validated("snap", 1, canonical=CANON,
                                            answer_of=lambda s: CANON)
        assert len(set(seen)) == len(seen) == 8

    def test_nested_analyses_drop_only_their_own(self, monkeypatch):
        dropped: list[str] = []
        monkeypatch.setattr(whatif, "drop_snapshot", lambda s: dropped.append(s))
        monkeypatch.setattr(
            neighbourhood, "extract_validated",
            lambda src, link_id, **kw: Extraction(
                snapshot_id=f"cf-{link_id}", source_snapshot_id=src,
                radius_m=5000.0, link_count=10, node_count=20, seconds=0.01,
                validated=True))
        with borrowed("snap", 1, canonical=CANON, answer_of=lambda s: CANON):
            with borrowed("snap", 2, canonical=CANON,
                          answer_of=lambda s: CANON):
                pass
            assert dropped == ["cf-2"], "the inner one, and only the inner one"
        assert dropped == ["cf-2", "cf-1"]


class TestOrphansAreSwept:
    """A process killed between extracting and dropping leaves a row nothing
    will ever clean up. It cannot be served, but it accumulates."""

    def test_only_transient_rows_older_than_the_cutoff(self, monkeypatch):
        now = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.timezone.utc)
        asked: dict = {}

        def _query(sql, params=None):
            asked["sql"], asked["params"] = sql, params
            return [{"snapshot_id": "cf-old"}]

        dropped: list[str] = []
        monkeypatch.setattr(neighbourhood.db, "query", _query)
        monkeypatch.setattr(whatif, "drop_snapshot", lambda s: dropped.append(s))

        assert neighbourhood.sweep_orphans(now=now) == dropped == ["cf-old"]
        assert "is_transient" in asked["sql"]
        assert asked["params"][0] == now - dt.timedelta(
            seconds=neighbourhood.ORPHAN_AFTER_SECONDS)

    def test_the_cutoff_cannot_catch_a_live_analysis(self):
        """Extraction is sub-second and a full run is bounded, so an hour is
        far outside anything in flight."""
        assert neighbourhood.ORPHAN_AFTER_SECONDS >= 600

    def test_it_sweeps_by_the_flag_not_by_the_id_prefix(self, monkeypatch):
        """A naming convention is not a fact. If something transient is ever
        named differently, the flag still finds it."""
        asked: dict = {}
        monkeypatch.setattr(neighbourhood.db, "query",
                            lambda sql, params=None: asked.setdefault("sql", sql) and [])
        monkeypatch.setattr(whatif, "drop_snapshot", lambda s: None)
        neighbourhood.sweep_orphans()
        assert "LIKE" not in asked["sql"]
