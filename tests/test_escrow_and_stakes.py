"""
Tests for the escrow/stake layer TrueStake adds on top of the
settlement core: funding at create/accept time, refunds (cancel,
never-accepted expiry, full-window expiry), automatic payout crediting
on a real verdict, the pull-payment withdraw() path (including
double-withdraw protection), and access control on every
money-affecting method.

gl.nondet.web.render / gl.nondet.exec_prompt are mocked exactly like
in test_end_to_end.py wherever a real resolve_agreement call is
needed to reach a payout; every other escrow behavior is exercised
directly without needing the settlement pipeline at all.
"""
import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    PARTY_A_ADDRESS,
    PARTY_B_ADDRESS,
    STRANGER_ADDRESS,
    call_payable,
    gl,
    make_contract,
    reset_transfers,
    set_caller,
    u256,
)

ONE_GEN = 10**18
MIN_STAKE = 10**15  # matches contract.py's MIN_STAKE_WEI


def iso_in(seconds_from_now: float) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=seconds_from_now
    )
    return dt.isoformat()


def create_kwargs(c, **overrides):
    kwargs = dict(
        party_b_address=PARTY_B_ADDRESS,
        teams="Manchester United vs Liverpool",
        match_date="2026-09-05",
        threshold_score="2.5",
        comparison="above",
        description="Escrow test agreement",
        resolution_deadline=iso_in(c.MIN_DEADLINE_LEAD_SECONDS + 5),
        required_source_domains=["espn.com", "bbc.com"],
    )
    kwargs.update(overrides)
    return kwargs


def create_funded(c, value=ONE_GEN, **overrides):
    """party_a creates an agreement, funded with `value` wei."""
    set_caller(PARTY_A_ADDRESS)
    return call_payable(c, "create_agreement", value, **create_kwargs(c, **overrides))


def create_and_accept(c, value=ONE_GEN, **overrides):
    """party_a creates, party_b accepts, both funded with `value` wei."""
    aid = create_funded(c, value=value, **overrides)
    set_caller(PARTY_B_ADDRESS)
    call_payable(c, "accept_agreement", value, aid)
    return aid


def force_past_deadline(c, aid):
    """Rewrite stored timestamps so the agreement's resolution_deadline
    (and, for expire tests, its whole resolution window) is already in
    the past, without waiting in real time."""
    record = json.loads(c.agreements[aid])
    record["resolution_deadline"] = iso_in(-5)
    record["resolution_window_closes_at"] = iso_in(c.RESOLUTION_WINDOW_SECONDS)
    c.agreements[aid] = json.dumps(record, sort_keys=True)


def force_window_closed(c, aid):
    """Rewrite stored timestamps so the ENTIRE resolution window
    (deadline + RESOLUTION_WINDOW_SECONDS) is already in the past."""
    record = json.loads(c.agreements[aid])
    record["resolution_deadline"] = iso_in(-c.RESOLUTION_WINDOW_SECONDS - 10)
    record["resolution_window_closes_at"] = iso_in(-5)
    c.agreements[aid] = json.dumps(record, sort_keys=True)


FINAL_HIGH_SCORING_CONTENT = (
    "Manchester United beat Liverpool 3-1 in a thrilling full-time "
    "final result at Old Trafford this afternoon, sealing the win."
)


def llm_high_scoring_response(_prompt, response_format="text"):
    return "TEAM_MATCH: Match\nFRESHNESS: Final\nSCORE: 3-1\nCOMPARISON: Above"


def resolve_with_mocked_evidence(c, aid, urls=None):
    urls = urls or ["https://espn.com/x", "https://bbc.com/sport/x"]
    with patch.object(
        gl.nondet.web, "render",
        side_effect=lambda url, mode="text": FINAL_HIGH_SCORING_CONTENT,
    ), patch.object(
        gl.nondet, "exec_prompt", side_effect=llm_high_scoring_response,
    ):
        return json.loads(c.resolve_agreement(aid, urls))


class CreateAgreementFundingTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_stake_amount_equals_value_sent(self):
        aid = create_funded(self.c, value=3 * ONE_GEN)
        record = json.loads(self.c.get_agreement(aid))
        self.assertEqual(record["stake_amount"], str(3 * ONE_GEN))

    def test_party_a_funded_flag_set_immediately(self):
        aid = create_funded(self.c)
        record = json.loads(self.c.get_agreement(aid))
        self.assertTrue(record["party_a_funded"])
        self.assertFalse(record["party_b_funded"])

    def test_contract_balance_increases_by_value_sent(self):
        create_funded(self.c, value=2 * ONE_GEN)
        self.assertEqual(int(self.c.get_contract_balance()), 2 * ONE_GEN)

    def test_zero_value_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "create_agreement", 0, **create_kwargs(self.c))

    def test_below_min_stake_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(
                self.c, "create_agreement", MIN_STAKE - 1, **create_kwargs(self.c)
            )

    def test_exactly_min_stake_accepted(self):
        set_caller(PARTY_A_ADDRESS)
        aid = call_payable(
            self.c, "create_agreement", MIN_STAKE, **create_kwargs(self.c)
        )
        record = json.loads(self.c.get_agreement(aid))
        self.assertEqual(record["stake_amount"], str(MIN_STAKE))

    def test_rejected_creation_does_not_move_balance(self):
        """A reverted create_agreement must leave the contract's
        balance untouched, mirroring atomic on-chain revert semantics
        (see call_payable's docstring)."""
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "create_agreement", 0, **create_kwargs(self.c))
        self.assertEqual(int(self.c.get_contract_balance()), 0)


class AcceptAgreementFundingTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.aid = create_funded(self.c, value=ONE_GEN)

    def test_exact_match_accepted(self):
        set_caller(PARTY_B_ADDRESS)
        record = json.loads(call_payable(self.c, "accept_agreement", ONE_GEN, self.aid))
        self.assertTrue(record["party_b_funded"])
        self.assertEqual(record["status"], "open")

    def test_contract_balance_doubles_on_accept(self):
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_agreement", ONE_GEN, self.aid)
        self.assertEqual(int(self.c.get_contract_balance()), 2 * ONE_GEN)

    def test_underpay_rejected(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "accept_agreement", ONE_GEN - 1, self.aid)

    def test_overpay_rejected(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "accept_agreement", 2 * ONE_GEN, self.aid)

    def test_rejected_accept_does_not_move_balance(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "accept_agreement", 2 * ONE_GEN, self.aid)
        # Only party_a's original stake should be reflected - the
        # rejected overpay must not have stuck.
        self.assertEqual(int(self.c.get_contract_balance()), ONE_GEN)

    def test_wrong_caller_cannot_accept_even_with_correct_value(self):
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "accept_agreement", ONE_GEN, self.aid)


class CancelRefundTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.aid = create_funded(self.c, value=ONE_GEN)

    def test_cancel_credits_party_a_only(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_agreement(self.aid)
        self.assertEqual(
            int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), ONE_GEN
        )
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), 0)

    def test_cancel_marks_refunded(self):
        set_caller(PARTY_A_ADDRESS)
        record = json.loads(self.c.cancel_agreement(self.aid))
        self.assertTrue(record["refunded"])
        self.assertEqual(record["status"], "cancelled")

    def test_only_party_a_can_cancel(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.cancel_agreement(self.aid)

    def test_cannot_cancel_once_open(self):
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_agreement", ONE_GEN, self.aid)
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.cancel_agreement(self.aid)


class ExpireRefundTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_never_accepted_refunds_party_a_only(self):
        aid = create_funded(self.c, value=ONE_GEN)
        force_past_deadline(self.c, aid)
        record = json.loads(self.c.expire_agreement(aid))
        self.assertEqual(record["status"], "expired")
        self.assertTrue(record["refunded"])
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), ONE_GEN)
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), 0)

    def test_open_window_closed_refunds_both_equally(self):
        aid = create_and_accept(self.c, value=ONE_GEN)
        force_window_closed(self.c, aid)
        record = json.loads(self.c.expire_agreement(aid))
        self.assertEqual(record["status"], "expired")
        self.assertTrue(record["refunded"])
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), ONE_GEN)
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), ONE_GEN)

    def test_cannot_expire_open_agreement_before_window_closes(self):
        aid = create_and_accept(self.c, value=ONE_GEN)
        with self.assertRaises(gl.vm.UserError):
            self.c.expire_agreement(aid)

    def test_cannot_expire_pending_agreement_before_deadline(self):
        aid = create_funded(self.c, value=ONE_GEN)
        with self.assertRaises(gl.vm.UserError):
            self.c.expire_agreement(aid)


class ResolvePayoutTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_winner_credited_full_pot(self):
        aid = create_and_accept(self.c, value=ONE_GEN)
        force_past_deadline(self.c, aid)
        result = resolve_with_mocked_evidence(self.c, aid)
        self.assertEqual(result["winner"], "party_a")
        self.assertTrue(result["payout_settled"])
        self.assertEqual(
            int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), 2 * ONE_GEN
        )
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), 0)

    def test_loser_gets_nothing_credited(self):
        aid = create_and_accept(self.c, value=ONE_GEN)
        force_past_deadline(self.c, aid)
        resolve_with_mocked_evidence(self.c, aid)
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), 0)

    def test_indeterminate_verdict_credits_nobody(self):
        aid = create_and_accept(self.c, value=ONE_GEN)
        force_past_deadline(self.c, aid)
        with patch.object(
            gl.nondet.web, "render", side_effect=lambda url, mode="text": "unrelated page"
        ), patch.object(
            gl.nondet, "exec_prompt",
            side_effect=lambda p, response_format="text":
                "TEAM_MATCH: Unclear\nFRESHNESS: Unknown\nSCORE: Unclear\nCOMPARISON: Unclear",
        ):
            result = json.loads(
                self.c.resolve_agreement(aid, ["https://espn.com/x", "https://bbc.com/x"])
            )
        self.assertEqual(result["winner"], "unresolved")
        self.assertFalse(result["payout_settled"])
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), 0)
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), 0)


class WithdrawTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def test_withdraw_sends_correct_amount_to_caller(self):
        aid = create_funded(self.c, value=ONE_GEN)
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_agreement(aid)
        self.c.withdraw()
        self.assertEqual(len(gl.evm.transfers), 1)
        self.assertEqual(gl.evm.transfers[0]["value"], u256(ONE_GEN))
        self.assertEqual(
            gl.evm.transfers[0]["to"].lower(), PARTY_A_ADDRESS.lower()
        )

    def test_withdraw_zeroes_balance_before_transfer(self):
        aid = create_funded(self.c, value=ONE_GEN)
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_agreement(aid)
        self.c.withdraw()
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), 0)

    def test_withdraw_with_nothing_owed_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.withdraw()
        self.assertEqual(len(gl.evm.transfers), 0)

    def test_double_withdraw_rejected(self):
        aid = create_funded(self.c, value=ONE_GEN)
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_agreement(aid)
        self.c.withdraw()
        with self.assertRaises(gl.vm.UserError):
            self.c.withdraw()
        # Only the first, legitimate withdrawal should have moved GEN.
        self.assertEqual(len(gl.evm.transfers), 1)

    def test_credits_from_multiple_agreements_are_additive(self):
        """Mirrors the live Studio observation: an address owed GEN
        from more than one agreement gets it all in a single
        withdraw() call, because pending_withdrawals is additive per
        address, not per agreement."""
        aid1 = create_funded(self.c, value=ONE_GEN)
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_agreement(aid1)

        aid2 = create_and_accept(self.c, value=2 * ONE_GEN)
        force_past_deadline(self.c, aid2)
        resolve_with_mocked_evidence(self.c, aid2)  # party_a wins => +4 GEN

        self.assertEqual(
            int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), ONE_GEN + 4 * ONE_GEN
        )
        set_caller(PARTY_A_ADDRESS)
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers[-1]["value"], u256(ONE_GEN + 4 * ONE_GEN))
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), 0)

    def test_withdraw_is_permissionless_for_own_balance(self):
        """Anyone may call withdraw() - there is no party check - but
        it only ever pays out the CALLER's own credited balance."""
        aid = create_funded(self.c, value=ONE_GEN)
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_agreement(aid)

        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.withdraw()  # stranger has nothing owed
        self.assertEqual(len(gl.evm.transfers), 0)

        set_caller(PARTY_A_ADDRESS)
        self.c.withdraw()
        self.assertEqual(len(gl.evm.transfers), 1)


if __name__ == "__main__":
    unittest.main()
