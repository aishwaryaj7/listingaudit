"""Scoring the photo check, with a small made-up table instead of real photos."""

import json

import pandas as pd
import pytest

from listingaudit.config import settings
from listingaudit.photos.evaluate import (
    add_vision,
    answers_file,
    ask_vision,
    by_difficulty,
    checkable,
    load_answers,
    outcome,
    scores,
)
from listingaudit.photos.vision import PhotoAnswer, VisionReply


def saved(offer_id: str, matches: bool, confidence: str = "high", colour: str = "red") -> dict:
    answer = PhotoAnswer(colour=colour, matches_listing=matches, confidence=confidence, reason="")
    return {"offer_id": offer_id, "answer": answer.model_dump()}


@pytest.fixture
def table():
    return pd.DataFrame(
        {
            "offer_id": ["P1", "P2", "P3", "P4", "P5"],
            "swapped": [True, True, True, False, False],
            "difficulty": ["easy", "hard", "hard", None, None],
            "clip": ["mismatch", "unclear", "match", "match", "unclear"],
        }
    )


@pytest.mark.parametrize(
    "swapped, verdict, expected",
    [
        (True, "mismatch", "caught"),
        (True, "match", "missed"),
        (False, "mismatch", "false alarm"),
        (False, "match", "correct"),
        (True, "unclear", "to reviewer"),
        (False, "unclear", "to reviewer"),
    ],
)
def test_outcomes(swapped, verdict, expected):
    assert outcome(swapped, verdict) == expected


def test_scores_count_each_outcome(table):
    counts = scores(table, "clip")
    assert counts.to_dict() == {
        "caught": 1,
        "missed": 1,
        "false alarm": 0,
        "to reviewer": 2,
        "correct": 1,
    }


def test_swaps_caught_by_difficulty(table):
    assert by_difficulty(table, "clip").to_dict() == {"easy": "1 of 1", "hard": "0 of 2"}


def test_the_vision_model_only_decides_what_clip_leaves_unclear(table):
    answers = {
        "P1": saved("P1", matches=True),  # CLIP was sure; the vision model is ignored
        "P2": saved("P2", matches=False),
        "P3": saved("P3", matches=False),
        "P4": saved("P4", matches=True),
        "P5": saved("P5", matches=False, confidence="medium"),
    }
    both = add_vision(table, answers)
    assert both["vision"].tolist() == ["match", "mismatch", "mismatch", "match", "unclear"]
    assert both["both"].tolist() == ["mismatch", "mismatch", "match", "match", "unclear"]
    assert scores(both, "both")["caught"] == 2


def test_only_listings_with_one_clear_colour_are_checked(make_offer):
    offers = [make_offer(color="Red"), make_offer(color=None), make_offer(color="Black/White")]
    assert [offer.color for offer in checkable(offers)] == ["Red"]


class FakeVision:
    model = "test-model"

    def __init__(self):
        self.asked = []

    def ask(self, path, noun, colour):
        self.asked.append(colour)
        answer = PhotoAnswer(colour="red", matches_listing=True, confidence="high", reason="Red.")
        return VisionReply(answer, input_tokens=100, output_tokens=40, seconds=0.25)


def test_vision_answers_are_saved_and_not_asked_for_twice(tmp_path, monkeypatch, make_offer):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    offers = [make_offer(offer_id="P0001", color="Red"), make_offer(offer_id="P0002", color="Tan")]
    vision = FakeVision()

    first = ask_vision(offers, vision)
    again = ask_vision(offers, vision)

    assert vision.asked == ["Red", "Tan"]
    assert set(first) == set(again) == {"P0001", "P0002"}
    lines = answers_file("test-model").read_text().splitlines()
    assert json.loads(lines[0])["input_tokens"] == 100
    assert load_answers("test-model")["P0002"]["answer"]["colour"] == "red"


def test_no_saved_answers_is_an_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert load_answers("never-asked") == {}
