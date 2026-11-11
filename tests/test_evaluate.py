"""Scoring the search methods on the test questions."""

import pandas as pd

from listingaudit.rulebook.evaluate import (
    answers_question,
    first_right_rank,
    load_questions,
    scores,
)
from listingaudit.rulebook.search import Hit


def hit(chunk_id: str) -> Hit:
    return Hit(chunk_id, "", None, "", "", 0.0)


def test_answers_question_matches_whole_articles():
    assert answers_question("gpsr/19", ["gpsr/19"])
    assert answers_question("pangv/4/1-2#2", ["pangv/4"])  # a part of § 4(1)-(2) is part of § 4
    assert answers_question("aldi_sued/29", ["aldi_sued"])  # any part of the judgment
    assert not answers_question("pangv/40", ["pangv/4"])
    assert not answers_question("gpsr/19", ["pid/19"])


def test_first_right_rank():
    hits = [hit("pid/2"), hit("pangv/4/1-2"), hit("pid/3")]
    assert first_right_rank(hits, ["pangv/4", "pid/3"]) == 2
    assert first_right_rank(hits, ["gpsr/19"]) is None


def test_scores():
    results = pd.DataFrame(
        {"question": list("abcd"), "method": ["meaning"] * 4, "rank": [1, 2, None, 4]}
    )
    assert scores(results).loc["meaning"].to_dict() == {
        "hit_at_1": 0.25,  # one of four questions found first
        "hit_at_5": 0.75,
        "mrr": 0.44,  # (1 + 1/2 + 0 + 1/4) / 4
    }


def test_questions_are_well_formed():
    questions = load_questions()
    assert len(questions) == 31
    assert all(q.question.endswith("?") and q.answers for q in questions)
