"""
Tests for the deterministic parsing/validation helpers: team/fixture
validation, domain extraction, endpoint-requirement parsing, and
score/threshold number parsing.
"""
import datetime
import unittest

from _bootstrap import PARTY_A_ADDRESS, PARTY_B_ADDRESS, gl, make_contract, set_caller


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


class TeamsValidationTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        set_caller(PARTY_A_ADDRESS)

    def test_valid_teams_accepted(self):
        aid = self.c.create_agreement(**create_kwargs(self.c))
        self.assertIsNotNone(aid)

    def test_missing_vs_separator_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, teams="Manchester United Liverpool"))

    def test_empty_team_a_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, teams=" vs Liverpool"))

    def test_empty_team_b_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, teams="Manchester United vs "))

    def test_identical_teams_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, teams="Liverpool vs Liverpool"))

    def test_identical_teams_case_insensitive_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, teams="Liverpool vs LIVERPOOL"))

    def test_overlong_team_name_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, teams=("X" * 100) + " vs Liverpool")
            )

    def test_teams_stored_normalized(self):
        aid = self.c.create_agreement(
            **create_kwargs(self.c, teams="  Manchester United   VS  Liverpool  ")
        )
        import json
        record = json.loads(self.c.get_agreement(aid))
        self.assertEqual(record["team_a"], "Manchester United")
        self.assertEqual(record["team_b"], "Liverpool")

    def test_empty_match_date_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, match_date="  "))

    def test_overlong_match_date_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, match_date="x" * 60))


class DomainExtractionTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_bare_domain(self):
        self.assertEqual(self.c._extract_domain("https://espn.com/nfl/scores"), "espn.com")

    def test_www_prefix_stripped(self):
        self.assertEqual(self.c._extract_domain("https://www.espn.com/nfl"), "espn.com")

    def test_http_scheme_accepted(self):
        self.assertEqual(self.c._extract_domain("http://espn.com"), "espn.com")

    def test_missing_scheme_rejected(self):
        self.assertEqual(self.c._extract_domain("espn.com/nfl"), "")

    def test_multi_part_suffix_domain(self):
        self.assertEqual(
            self.c._extract_domain("https://www.sportsmole.co.uk/football/x"), "sportsmole.co.uk"
        )

    def test_query_and_fragment_stripped(self):
        self.assertEqual(
            self.c._extract_domain("https://espn.com/nfl?x=1#frag"), "espn.com"
        )

    def test_port_stripped(self):
        self.assertEqual(self.c._extract_domain("https://espn.com:8080/nfl"), "espn.com")

    def test_overlong_url_rejected(self):
        self.assertEqual(self.c._extract_domain("https://espn.com/" + "a" * 3000), "")

    def test_empty_string(self):
        self.assertEqual(self.c._extract_domain(""), "")


class EndpointRequirementParsingTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_bare_domain_no_path(self):
        self.assertEqual(self.c._parse_endpoint_requirement("bbc.com"), ("bbc.com", ""))

    def test_domain_with_path_no_scheme(self):
        self.assertEqual(self.c._parse_endpoint_requirement("bbc.com/sport"), ("bbc.com", "/sport"))

    def test_full_url_with_path(self):
        self.assertEqual(
            self.c._parse_endpoint_requirement("https://bbc.com/sport/football"),
            ("bbc.com", "/sport/football"),
        )

    def test_trailing_slash_stripped(self):
        self.assertEqual(self.c._parse_endpoint_requirement("bbc.com/sport/"), ("bbc.com", "/sport"))

    def test_empty_entry(self):
        self.assertEqual(self.c._parse_endpoint_requirement(""), ("", ""))

    def test_case_normalized(self):
        self.assertEqual(self.c._parse_endpoint_requirement("BBC.COM/Sport"), ("bbc.com", "/sport"))


class RequiredSourceDomainsValidationTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        set_caller(PARTY_A_ADDRESS)

    def test_min_two_domains_required(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, required_source_domains=["espn.com"])
            )

    def test_empty_list_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, required_source_domains=[]))

    def test_too_many_domains_rejected(self):
        domains = list(self.c.REPUTABLE_SPORTS_DOMAINS)[:7]
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, required_source_domains=domains)
            )

    def test_max_six_domains_accepted(self):
        domains = sorted(self.c.REPUTABLE_SPORTS_DOMAINS)[:6]
        aid = self.c.create_agreement(
            **create_kwargs(self.c, required_source_domains=domains)
        )
        self.assertIsNotNone(aid)

    def test_unreputable_domain_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(
                    self.c, required_source_domains=["espn.com", "some-random-blog.com"]
                )
            )

    def test_duplicate_domain_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(
                    self.c, required_source_domains=["espn.com", "espn.com"]
                )
            )

    def test_duplicate_domain_different_paths_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(
                    self.c,
                    required_source_domains=["bbc.com/sport", "bbc.com/sport/football"],
                )
            )

    def test_domain_with_path_accepted(self):
        aid = self.c.create_agreement(
            **create_kwargs(
                self.c, required_source_domains=["espn.com", "bbc.com/sport"]
            )
        )
        self.assertIsNotNone(aid)

    def test_blank_entry_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, required_source_domains=["espn.com", "   "])
            )

    def test_unparseable_entry_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(
                **create_kwargs(self.c, required_source_domains=["espn.com", "://bad"])
            )

    def test_domains_stored_sorted(self):
        import json
        aid = self.c.create_agreement(
            **create_kwargs(self.c, required_source_domains=["nba.com", "espn.com"])
        )
        record = json.loads(self.c.get_agreement(aid))
        self.assertEqual(record["required_source_domains"], sorted(["nba.com", "espn.com"]))

    def test_all_allowlisted_domains_are_valid_entries(self):
        # Every REPUTABLE_SPORTS_DOMAINS entry must itself round-trip
        # through _extract_domain, or it would be a silent dead entry.
        for domain in self.c.REPUTABLE_SPORTS_DOMAINS:
            self.assertEqual(self.c._extract_domain(f"https://{domain}/x"), domain)


class ScoreParsingTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_simple_score(self):
        self.assertEqual(self.c._parse_score("3-1"), 4.0)

    def test_score_with_spaces(self):
        self.assertEqual(self.c._parse_score("3 - 1"), 4.0)

    def test_zero_zero(self):
        self.assertEqual(self.c._parse_score("0-0"), 0.0)

    def test_score_with_trailing_text(self):
        self.assertEqual(self.c._parse_score("3-1 (FT)"), 4.0)

    def test_double_digit_scores(self):
        self.assertEqual(self.c._parse_score("12-10"), 22.0)

    def test_empty_string(self):
        self.assertIsNone(self.c._parse_score(""))

    def test_none_input(self):
        self.assertIsNone(self.c._parse_score(None))

    def test_single_number_rejected(self):
        self.assertIsNone(self.c._parse_score("3"))

    def test_third_number_ambiguous(self):
        self.assertIsNone(self.c._parse_score("3-1-2"))

    def test_leading_negative_rejected(self):
        self.assertIsNone(self.c._parse_score("-1-2"))

    def test_no_leading_digit_rejected(self):
        self.assertIsNone(self.c._parse_score("three-one"))

    def test_literal_unclear_rejected(self):
        self.assertIsNone(self.c._parse_score("Unclear"))

    def test_missing_separator_rejected(self):
        self.assertIsNone(self.c._parse_score("31"))

    def test_dangling_hyphen_rejected(self):
        self.assertIsNone(self.c._parse_score("3-"))


class ThresholdNumberParsingTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_decimal_threshold(self):
        self.assertEqual(self.c._parse_plain_number("2.5"), 2.5)

    def test_integer_threshold(self):
        self.assertEqual(self.c._parse_plain_number("3"), 3.0)

    def test_zero_threshold(self):
        self.assertEqual(self.c._parse_plain_number("0"), 0.0)

    def test_negative_rejected(self):
        self.assertIsNone(self.c._parse_plain_number("-1"))

    def test_non_numeric_rejected(self):
        self.assertIsNone(self.c._parse_plain_number("many"))

    def test_second_number_in_remainder_rejected(self):
        self.assertIsNone(self.c._parse_plain_number("2.5 or 3"))

    def test_empty_rejected(self):
        self.assertIsNone(self.c._parse_plain_number(""))

    def test_none_rejected(self):
        self.assertIsNone(self.c._parse_plain_number(None))

    def test_leading_dot_rejected(self):
        self.assertIsNone(self.c._parse_plain_number(".5"))


class ThresholdScoreValidationOnCreateTests(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        set_caller(PARTY_A_ADDRESS)

    def test_decimal_threshold_accepted(self):
        aid = self.c.create_agreement(**create_kwargs(self.c, threshold_score="2.5"))
        self.assertIsNotNone(aid)

    def test_integer_threshold_accepted(self):
        aid = self.c.create_agreement(**create_kwargs(self.c, threshold_score="3"))
        self.assertIsNotNone(aid)

    def test_garbage_threshold_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, threshold_score="lots"))

    def test_negative_threshold_rejected(self):
        with self.assertRaises(gl.vm.UserError):
            self.c.create_agreement(**create_kwargs(self.c, threshold_score="-1"))


if __name__ == "__main__":
    unittest.main()
