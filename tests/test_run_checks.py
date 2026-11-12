"""All checks on whole splits, scored against the answer key.

These run on a made-up catalogue, and also on the real ABO products when the
database is available. Only the practice split is used.
"""

from collections import defaultdict
from typing import get_args

import psycopg
import pytest

from listingaudit.checks.evaluate import CAUGHT_BY, SELF_DECIDING, by_rule, by_trap, outcomes
from listingaudit.checks.run import CODE_RULES, load_listings, run_checks
from listingaudit.db import connect, connect_as_agent
from listingaudit.market.build import build_splits
from listingaudit.market.catalogue import load_catalogue
from listingaudit.market.listing import PROBLEM_RULE
from listingaudit.market.models import ProblemType
from listingaudit.market.rules import Rule
from listingaudit.rulebook.checklist import load_checklist


def test_every_rule_checked_by_code_has_a_check():
    by_code = [rule for rule, spec in load_checklist().rules.items() if spec.checked_by == "code"]
    assert sorted(CODE_RULES) == sorted(by_code)


def test_every_problem_code_can_find_is_scored():
    found_by_code = {p for p in get_args(ProblemType) if PROBLEM_RULE[p] in CODE_RULES}
    assert set(CAUGHT_BY) == found_by_code


def test_rules_without_a_code_check_are_skipped(make_offer):
    assert run_checks(make_offer(), [], [Rule.PHOTO_MATCHES_TEXT]) == []


@pytest.fixture(scope="module", params=["made_up", "abo"])
def practice(request):
    if request.param == "made_up":
        catalogue = request.getfixturevalue("made_up")
    else:
        try:
            with connect() as conn:
                catalogue = load_catalogue(conn)
        except psycopg.Error:
            pytest.skip("the database is not running")
        if not catalogue[0]:
            pytest.skip("no ABO products in the database")
    return build_splits(*catalogue)["practice"]


@pytest.fixture(scope="module")
def results(practice):
    histories = defaultdict(list)
    for point in practice.prices:
        histories[point.offer_id].append(point)
    listings = [(offer, histories[offer.offer_id]) for offer in practice.offers]
    return outcomes(listings, practice.cases, practice.problems)


def test_every_planted_problem_is_caught_except_the_wrong_warnings(results):
    missed = results[results["outcome"] == "missed"]
    assert set(missed["problem"]) <= {"missing_safety_warnings"}
    assert set(missed["difficulty"]) <= {"hard"}  # the warnings shown are the wrong ones
    assert by_rule(results).loc[str(Rule.PRIOR_PRICE), "caught"] > 0


def test_there_are_no_false_alarms(results):
    assert results["outcome"].isin(["false alarm", "cannot check"]).sum() == 0


def test_the_traps_set_off_no_alarms(practice, results):
    assert by_trap(results, practice.traps)["false alarms"].sum() == 0


def test_checks_that_decide_for_themselves_agree_with_the_answer_key(results):
    mine = results[results["rule"].isin([str(rule) for rule in SELF_DECIDING])]
    assert (mine["status"].eq("not_applicable") == ~mine["rule_applies"]).all()


def test_the_agent_login_can_load_listings_with_their_price_history():
    try:
        with connect_as_agent() as conn:
            listings = load_listings(conn, offer_ids=["P0001", "E0001"])
    except psycopg.Error:
        pytest.skip("the database is not running")
    if not listings:
        pytest.skip("the marketplace is not built")
    assert [offer.offer_id for offer, _ in listings] == ["E0001", "P0001"]
    assert all(len(history) == 90 for _, history in listings)
