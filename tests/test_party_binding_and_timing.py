"""
Tests for party binding (create/accept/cancel) and deadline/timing
enforcement (min/max lead, resolution window, expire_agreement).
"""
import datetime
import json
import unittest

from _bootstrap import (
    PARTY_A_ADDRESS,
    PARTY_B_ADDRESS,
    STRANGER_ADDRESS,
    gl,
    make_contract,
    set_caller,
)


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
        description="Total goals over/under",
        resolution_deadline=iso_in(c.MIN_DEADLINE_LEAD_SECONDS + 5),
        required_source_domains=["espn.com", "bbc.com"],
    )
    kwargs.update(overrides)
    return kwargs


class CreateAgreementPartyBindingTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_party_a_is_caller(self):
        set_caller(PARTY_A_ADDRESS)
        aid = self.c.create_agreement(**create_kwargs(self.c))
        record = json.loads(self.c.get_agreement(aid))
        self.assertEqual(record["party_a"].lower(), PARTY_A_ADDRESS.lower())

    def test_party_b_is_supplied_address(self):
        set_caller(PARTY_A_ADDRESS)
        aid = self.c.create_agreement(**create_kwargs(self.c))
        record = json.loads(self.c.get_agreement(aid))
        self.assertEqual(record["party_b"].lower(), PARTY_B_ADDRESS.lower())

    def test_party_a_cannot_equal_party_b(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, party_b_address=PARTY_A_ADDRESS))

    def test_party_a_cannot_equal_party_b_case_insensitive(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, party_b_address=PARTY_A_ADDRESS.upper())
            )

    def test_invalid_party_b_address_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, party_b_address="not-an-address"))

    def test_status_starts_pending_acceptance(self):
        set_caller(PARTY_A_ADDRESS)
        aid = self.c.create_agreement(**create_kwargs(self.c))
        record = json.loads(self.c.get_agreement(aid))
        self.assertEqual(record["status"], "pending_acceptance")

    def test_agreement_ids_increment(self):
        set_caller(PARTY_A_ADDRESS)
        aid1 = self.c.create_agreement(**create_kwargs(self.c))
        aid2 = self.c.create_agreement(**create_kwargs(self.c))
        self.assertNotEqual(aid1, aid2)
        self.assertEqual(self.c.total_agreements(), 2)

    def test_empty_description_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, description="   "))

    def test_overlong_description_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, description="x" * 500))

    def test_comparison_must_be_above_or_below(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, comparison="sideways"))

    def test_comparison_case_insensitive(self):
        set_caller(PARTY_A_ADDRESS)
        aid = self.c.create_agreement(**create_kwargs(self.c, comparison="ABOVE"))
        record = json.loads(self.c.get_agreement(aid))
        self.assertEqual(record["comparison"], "above")


class AcceptAgreementTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        self.aid = self.c.create_agreement(**create_kwargs(self.c))

    def test_only_party_b_can_accept(self):
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.accept_agreement(self.aid)

    def test_party_a_cannot_accept_own_agreement(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.accept_agreement(self.aid)

    def test_party_b_accept_moves_to_open(self):
        set_caller(PARTY_B_ADDRESS)
        result = json.loads(self.c.accept_agreement(self.aid))
        self.assertEqual(result["status"], "open")
        self.assertIsNotNone(result["accepted_at"])

    def test_double_accept_rejected(self):
        set_caller(PARTY_B_ADDRESS)
        self.c.accept_agreement(self.aid)
        with self.assertRaises(gl.vm.UserError):
            self.c.accept_agreement(self.aid)

    def test_accept_case_insensitive_address(self):
        set_caller("0x" + PARTY_B_ADDRESS[2:].upper())
        result = json.loads(self.c.accept_agreement(self.aid))
        self.assertEqual(result["status"], "open")

    def test_accept_after_deadline_rejected(self):
        record = json.loads(self.c.agreements[self.aid])
        record["resolution_deadline"] = iso_in(-5)
        self.c.agreements[self.aid] = json.dumps(record, sort_keys=True)
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.accept_agreement(self.aid)

    def test_accept_nonexistent_agreement(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.accept_agreement("999")


class CancelAgreementTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        self.aid = self.c.create_agreement(**create_kwargs(self.c))

    def test_party_a_can_cancel_pending(self):
        set_caller(PARTY_A_ADDRESS)
        result = json.loads(self.c.cancel_agreement(self.aid))
        self.assertEqual(result["status"], "cancelled")

    def test_party_b_cannot_cancel(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.cancel_agreement(self.aid)

    def test_stranger_cannot_cancel(self):
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.cancel_agreement(self.aid)

    def test_cannot_cancel_after_accept(self):
        set_caller(PARTY_B_ADDRESS)
        self.c.accept_agreement(self.aid)
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.cancel_agreement(self.aid)

    def test_cannot_double_cancel(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_agreement(self.aid)
        with self.assertRaises(gl.vm.UserError):
            self.c.cancel_agreement(self.aid)


class DeadlineLeadTimeTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_deadline_too_soon_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, resolution_deadline=iso_in(60))
            )

    def test_deadline_at_exactly_min_lead_accepted(self):
        set_caller(PARTY_A_ADDRESS)
        aid = self.c.create_agreement(
            **create_kwargs(
                self.c,
                resolution_deadline=iso_in(self.c.MIN_DEADLINE_LEAD_SECONDS + 2),
            )
        )
        self.assertIsNotNone(aid)

    def test_deadline_too_far_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(
                    self.c,
                    resolution_deadline=iso_in(self.c.MAX_DEADLINE_LEAD_SECONDS + 3600),
                )
            )

    def test_deadline_within_max_accepted(self):
        set_caller(PARTY_A_ADDRESS)
        aid = self.c.create_agreement(
            **create_kwargs(
                self.c,
                resolution_deadline=iso_in(self.c.MAX_DEADLINE_LEAD_SECONDS - 3600),
            )
        )
        self.assertIsNotNone(aid)

    def test_deadline_in_past_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, resolution_deadline=iso_in(-3600))
            )

    def test_invalid_deadline_format_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, resolution_deadline="not-a-date")
            )

    def test_deadline_accepts_trailing_z(self):
        set_caller(PARTY_A_ADDRESS)
        dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=self.c.MIN_DEADLINE_LEAD_SECONDS + 100
        )
        z_form = dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        aid = self.c.create_agreement(
            **create_kwargs(self.c, resolution_deadline=z_form)
        )
        self.assertIsNotNone(aid)

    def test_min_deadline_lead_is_two_hours(self):
        self.assertEqual(self.c.MIN_DEADLINE_LEAD_SECONDS, 7200)

    def test_max_deadline_lead_is_seven_days(self):
        self.assertEqual(self.c.MAX_DEADLINE_LEAD_SECONDS, 604800)

    def test_resolution_window_is_24_hours(self):
        self.assertEqual(self.c.RESOLUTION_WINDOW_SECONDS, 86400)


class ExpireAgreementTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        self.aid = self.c.create_agreement(**create_kwargs(self.c))

    def test_cannot_expire_before_deadline(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.expire_agreement(self.aid)

    def test_pending_expires_after_deadline(self):
        record = json.loads(self.c.agreements[self.aid])
        record["resolution_deadline"] = iso_in(-5)
        self.c.agreements[self.aid] = json.dumps(record, sort_keys=True)
        result = json.loads(self.c.expire_agreement(self.aid))
        self.assertEqual(result["status"], "expired")

    def test_anyone_can_expire(self):
        record = json.loads(self.c.agreements[self.aid])
        record["resolution_deadline"] = iso_in(-5)
        self.c.agreements[self.aid] = json.dumps(record, sort_keys=True)
        set_caller(STRANGER_ADDRESS)
        result = json.loads(self.c.expire_agreement(self.aid))
        self.assertEqual(result["status"], "expired")

    def test_open_agreement_cannot_expire_before_window_closes(self):
        set_caller(PARTY_B_ADDRESS)
        self.c.accept_agreement(self.aid)
        record = json.loads(self.c.agreements[self.aid])
        record["resolution_deadline"] = iso_in(-5)
        record["resolution_window_closes_at"] = iso_in(self.c.RESOLUTION_WINDOW_SECONDS)
        self.c.agreements[self.aid] = json.dumps(record, sort_keys=True)
        with self.assertRaises(gl.vm.UserError):
            self.c.expire_agreement(self.aid)

    def test_open_agreement_expires_after_window_closes(self):
        set_caller(PARTY_B_ADDRESS)
        self.c.accept_agreement(self.aid)
        record = json.loads(self.c.agreements[self.aid])
        record["resolution_deadline"] = iso_in(-100000)
        record["resolution_window_closes_at"] = iso_in(-5)
        self.c.agreements[self.aid] = json.dumps(record, sort_keys=True)
        result = json.loads(self.c.expire_agreement(self.aid))
        self.assertEqual(result["status"], "expired")

    def test_resolved_agreement_cannot_expire(self):
        record = json.loads(self.c.agreements[self.aid])
        record["status"] = "resolved"
        self.c.agreements[self.aid] = json.dumps(record, sort_keys=True)
        with self.assertRaises(gl.vm.UserError):
            self.c.expire_agreement(self.aid)

    def test_cancelled_agreement_cannot_expire(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_agreement(self.aid)
        with self.assertRaises(gl.vm.UserError):
            self.c.expire_agreement(self.aid)

    def test_expire_nonexistent_agreement(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.expire_agreement("999")


class GetRoleAndViewsTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        self.aid = self.c.create_agreement(**create_kwargs(self.c))

    def test_get_role_party_a(self):
        self.assertEqual(self.c.get_role(self.aid, PARTY_A_ADDRESS), "party_a")

    def test_get_role_party_b(self):
        self.assertEqual(self.c.get_role(self.aid, PARTY_B_ADDRESS), "party_b")

    def test_get_role_stranger(self):
        self.assertEqual(self.c.get_role(self.aid, STRANGER_ADDRESS), "none")

    def test_get_role_case_insensitive(self):
        mixed_case = "0x" + PARTY_A_ADDRESS[2:].upper()
        self.assertEqual(self.c.get_role(self.aid, mixed_case), "party_a")

    def test_get_agreement_nonexistent(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.get_agreement("999")

    def test_get_role_nonexistent_agreement(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.get_role("999", PARTY_A_ADDRESS)

    def test_total_agreements_reflects_count(self):
        self.assertEqual(self.c.total_agreements(), 1)
        set_caller(PARTY_A_ADDRESS)
        self.c.create_agreement(**create_kwargs(self.c))
        self.assertEqual(self.c.total_agreements(), 2)


if __name__ == "__main__":
    unittest.main()
