"""The ingest command's defaults must be able to succeed.

`--crossing-policy` defaulted to `confirmed`, which `split_at_junctions`
refuses unless `research=True` - and `ingest.run` never passes it. So
`nzcl-ingest --national`, exactly as the README gives it, downloaded all
272,441 features and then raised. The failure came AFTER the download, so the
cost of the broken default was the whole extract.

Nothing caught it: CI builds synthetic fixtures through `nzcl.fixtures` and
never runs the command, and `run()`'s own default was correct all along. Only
the command line disagreed with the library it drives.

These tests are cheap and pin the property that matters - the defaults compose
into something that can actually run - rather than the specific string.
"""

from __future__ import annotations

import inspect
import pytest

from nzcl import ingest, topology




class TestTheCrossingPolicyDefault:

    def test_the_default_is_the_canonical_policy(self):
        source = inspect.getsource(ingest.main)

        assert "default=CANONICAL_CROSSING_POLICY" in source, (
            "the CLI default must be the canonical policy constant, not a "
            "literal that can drift away from it")

    def test_the_canonical_policy_is_not_a_research_policy(self):
        """The property the old default violated."""
        assert topology.CANONICAL_CROSSING_POLICY not in \
            topology.RESEARCH_CROSSING_POLICIES

    def test_the_canonical_policy_is_accepted_without_research(self):
        """`split_at_junctions` must accept the default the CLI now passes.

        A one-link network is enough: this is asking whether the policy is
        permitted, not what it does.
        """
        sources = [
            topology.SourceLink(amds_id="A", coords=[(0.0, 0.0), (100.0, 0.0)],
                                attrs={}),
        ]

        result = topology.split_at_junctions(
            sources, crossing_policy=topology.CANONICAL_CROSSING_POLICY)

        assert result.crossing_policy == topology.CANONICAL_CROSSING_POLICY

    @pytest.mark.parametrize("policy", sorted(topology.RESEARCH_CROSSING_POLICIES))
    def test_a_research_policy_is_still_refused_without_research(self, policy):
        """The guard must keep working. Fixing the default must not have been
        done by loosening what it defaults away from."""
        sources = [
            topology.SourceLink(amds_id="A", coords=[(0.0, 0.0), (100.0, 0.0)],
                                attrs={}),
        ]

        with pytest.raises(ValueError, match="research=True"):
            topology.split_at_junctions(sources, crossing_policy=policy)

    def test_run_and_the_cli_agree_on_the_default(self):
        """They disagreed, and the library was the one that was right."""
        signature = inspect.signature(ingest.run)

        assert signature.parameters["crossing_policy"].default == \
            topology.CANONICAL_CROSSING_POLICY


def _rendered_help() -> str:
    """What a user actually sees, with whitespace normalised.

    Asserted against the RENDERED help rather than the source: in the source
    the sentence is split across adjacent string literals, so a substring check
    there fails on a quote boundary that does not exist at runtime. The
    rendered text is also the thing that was wrong - a reader following it
    downloaded the country and then hit the guard.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "nzcl.ingest", "--help"],
        capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return " ".join(out.stdout.split())


class TestTheHelpTextDescribesTheCodeThatRuns:

    def test_it_no_longer_calls_a_research_policy_canonical(self):
        text = _rendered_help()

        assert "'evidence' (the default) is the canonical graph" in text
        assert "'confirmed' (the default)" not in text

    def test_it_warns_that_the_research_policies_will_be_refused(self):
        """The old help gave no hint that two of its four choices cannot run."""
        text = _rendered_help()

        assert "RESEARCH policies" in text
        assert "research=True" in text

    def test_the_rendered_choices_still_offer_all_four(self):
        """Fixing the default must not have removed the research policies -
        they exist so the rejected strategy stays reproducible."""
        text = _rendered_help()

        for policy in topology.CROSSING_POLICIES:
            assert policy in text


class TestTheProcessingVersionCommentMatchesThePolicy:
    """The comment said 2.1.0 nodes the classifier's AT_GRADE crossings.

    That is the strategy the promotion gate rejected. A reader taking the
    comment at face value would expect rural crossroads to be joined in any
    2.1.0 graph, which is false whenever the override table is empty - and it
    is empty by default.
    """

    def test_it_describes_the_evidence_policy(self):
        from nzcl import config

        comment = inspect.getsource(config)
        block = comment[comment.index("2.1.0 -"):comment.index("PROCESSING_VERSION =")]

        assert "EVIDENCE-BACKED OVERRIDE" in block
        assert "REJECTED" in block
        assert "no interior crossing is noded at all" in block
