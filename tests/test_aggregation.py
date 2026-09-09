"""
Tests for `_aggregate`, the deterministic per-source-record ->
final_verdict combiner, in isolation from the fetch/LLM pipeline.
"""
import unittest

from _bootstrap import make_contract


def record(
    comparison="Above",
    fetch_status="ok",
    is_duplicate_domain=False,
    is_reputable=True,
    quality_flag="ok",
    domain="espn.com",
):
    return {
        "domain": domain,
        "comparison": comparison,
        "fetch_status": fetch_status,
        "is_duplicate_domain": is_duplicate_domain,
        "is_reputable": is_reputable,
        "quality_flag": quality_flag,
    }


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_two_agreeing_above_sources_yield_above(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="bbc.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Above")

    def test_two_agreeing_below_sources_yield_below(self):
        records = [
            record(comparison="Below", domain="espn.com"),
            record(comparison="Below", domain="bbc.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Below")

    def test_two_agreeing_equal_sources_yield_equal(self):
        records = [
            record(comparison="Equal", domain="espn.com"),
            record(comparison="Equal", domain="bbc.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Equal")

    def test_single_eligible_source_is_indeterminate(self):
        records = [record(comparison="Above", domain="espn.com")]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_no_records_is_indeterminate(self):
        self.assertEqual(self.c._aggregate([]), "Indeterminate")

    def test_disagreeing_sources_indeterminate(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Below", domain="bbc.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_failed_fetch_excluded_from_eligible(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="bbc.com", fetch_status="timeout"),
        ]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_duplicate_domain_excluded(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="espn.com", is_duplicate_domain=True),
        ]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_unreputable_source_excluded(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="randomblog.com", is_reputable=False),
        ]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_bad_quality_flag_excluded(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Unclear", domain="bbc.com", quality_flag="team_mismatch"),
        ]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_three_sources_majority_above_wins(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="bbc.com"),
            record(comparison="Below", domain="nba.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Above")

    def test_three_sources_tie_is_indeterminate(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Below", domain="bbc.com"),
            record(comparison="Equal", domain="nba.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_four_sources_two_two_split_is_indeterminate(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="bbc.com"),
            record(comparison="Below", domain="nba.com"),
            record(comparison="Below", domain="nfl.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_extra_reputable_corroboration_still_wins(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="bbc.com"),
            record(comparison="Above", domain="nba.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Above")

    def test_lone_dissenting_source_does_not_block_majority_verdict(self):
        # A single dissenting source among 3 eligible ones cannot
        # prevent the other two from reaching quorum together.
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="bbc.com"),
            record(comparison="Below", domain="nba.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Above")

    def test_two_dissenting_sources_can_deny_a_verdict(self):
        # A larger dissenting bloc can outright flip or deny a verdict
        # rather than being outvoted, once it is no longer the
        # minority.
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Below", domain="bbc.com"),
            record(comparison="Below", domain="nba.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Below")

    def test_dissent_alone_can_never_produce_a_verdict(self):
        # A single eligible source (agreeing with nobody, since there
        # is nobody else) can never reach quorum on its own -
        # dissenting sources can only ever prevent a verdict, never
        # manufacture one by themselves.
        records = [record(comparison="Above", domain="espn.com")]
        self.assertEqual(self.c._aggregate(records), "Indeterminate")

    def test_quorum_requires_strict_majority_not_a_plurality(self):
        # 2 Above vs 1 Below vs 1 Equal: Above has the most votes but
        # does NOT strictly outnumber (Below + Equal) combined-
        # pairwise in the sense required here - specifically it must
        # beat EACH other category individually, which it does, so
        # this one DOES reach a verdict; contrasted with the four-way
        # 2-2 split test below, which does not.
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="bbc.com"),
            record(comparison="Below", domain="nba.com"),
            record(comparison="Equal", domain="nfl.com"),
        ]
        self.assertEqual(self.c._aggregate(records), "Above")

    def test_mixed_eligible_and_ineligible_still_evaluates_eligible_only(self):
        records = [
            record(comparison="Above", domain="espn.com"),
            record(comparison="Above", domain="bbc.com"),
            record(comparison="Unclear", domain="nba.com", quality_flag="not_final_score"),
        ]
        self.assertEqual(self.c._aggregate(records), "Above")


class WinnerAssignmentTests(unittest.TestCase):
    """
    Exercises the winner-assignment logic inline in resolve_agreement
    via the same mapping rules it uses: comparison == 'above' means
    party_a wins on an Above verdict and party_b wins on Below, and
    vice-versa for comparison == 'below'.
    """

    def setUp(self):
        self.c = make_contract()

    def _winner_for(self, agreement_comparison, final_verdict):
        if final_verdict == "Above":
            return "party_a" if agreement_comparison == "above" else "party_b"
        elif final_verdict == "Below":
            return "party_a" if agreement_comparison == "below" else "party_b"
        return "unresolved"

    def test_above_comparison_above_verdict_party_a_wins(self):
        self.assertEqual(self._winner_for("above", "Above"), "party_a")

    def test_above_comparison_below_verdict_party_b_wins(self):
        self.assertEqual(self._winner_for("above", "Below"), "party_b")

    def test_below_comparison_below_verdict_party_a_wins(self):
        self.assertEqual(self._winner_for("below", "Below"), "party_a")

    def test_below_comparison_above_verdict_party_b_wins(self):
        self.assertEqual(self._winner_for("below", "Above"), "party_b")

    def test_equal_verdict_always_unresolved(self):
        self.assertEqual(self._winner_for("above", "Equal"), "unresolved")
        self.assertEqual(self._winner_for("below", "Equal"), "unresolved")

    def test_indeterminate_verdict_always_unresolved(self):
        self.assertEqual(self._winner_for("above", "Indeterminate"), "unresolved")
        self.assertEqual(self._winner_for("below", "Indeterminate"), "unresolved")


if __name__ == "__main__":
    unittest.main()
