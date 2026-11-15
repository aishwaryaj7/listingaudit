"""The photo check's decisions, with made-up CLIP scores and vision answers."""

import pytest

from listingaudit.config import settings
from listingaudit.photos.check import MATCH_MIN, MISMATCH_MAX, check_photo, judge
from listingaudit.photos.colours import FAMILIES
from listingaudit.photos.vision import PhotoAnswer, VisionReply


def spread(**given: float) -> dict[str, float]:
    """Scores for every family: the given ones, and the rest shared out evenly."""
    rest = (1 - sum(given.values())) / (len(FAMILIES) - len(given))
    return {family: given.get(family, rest) for family in FAMILIES}


class FakeClip:
    def __init__(self, scores: dict[str, float]):
        self.scores = scores
        self.asked = []

    def colours(self, path, noun):
        self.asked.append((path, noun))
        return self.scores


class FakeVision:
    def __init__(self, answer: PhotoAnswer):
        self.answer = answer
        self.asked = []

    def ask(self, path, noun, colour):
        self.asked.append((path, noun, colour))
        return VisionReply(self.answer, input_tokens=100, output_tokens=40, seconds=0.5)


@pytest.fixture
def photo(tmp_path, monkeypatch, make_offer):
    """A listing whose photo file exists."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "lamp.jpg").write_bytes(b"not read by the fake model")
    return lambda **changes: make_offer(
        product_type="LAMP", image_file="images/lamp.jpg", **changes
    )


# --- Deciding from the scores ------------------------------------------------------


def test_the_listing_colour_on_top_is_a_match():
    assert judge(spread(grey=0.40), "grey").outcome == "match"


def test_a_listing_colour_with_a_fair_score_is_a_match():
    verdict = judge(spread(black=0.6, grey=MATCH_MIN), "grey")
    assert (verdict.outcome, verdict.photo_colour) == ("match", "black")


def test_a_far_colour_on_top_with_almost_nothing_left_for_the_listing_colour_is_a_mismatch():
    verdict = judge(spread(red=0.95, grey=0.004), "grey")
    assert verdict.listing_score < MISMATCH_MAX
    assert (verdict.outcome, verdict.photo_colour) == ("mismatch", "red")


def test_a_near_colour_on_top_is_never_a_mismatch():
    # Cream that looks beige in a photo is a question for a person, not a finding
    verdict = judge(spread(beige=0.97, white=0.001), "white")
    assert (verdict.outcome, verdict.photo_colour) == ("unclear", "beige")


def test_a_far_colour_on_top_with_some_score_left_for_the_listing_colour_is_unclear():
    verdict = judge(spread(red=0.9, grey=0.05), "grey")
    assert MISMATCH_MAX <= verdict.listing_score < MATCH_MIN
    assert verdict.outcome == "unclear"


def test_the_thresholds_can_be_changed_for_tuning():
    scores = spread(red=0.9, grey=0.05)
    assert judge(scores, "grey", mismatch_max=0.1).outcome == "mismatch"
    assert judge(scores, "grey", match_min=0.05).outcome == "match"


# --- The check -----------------------------------------------------------------------


def test_a_listing_without_a_colour_is_not_applicable(make_offer):
    result = check_photo(make_offer(color=None), clip=FakeClip({}))
    assert result.status == "not_applicable"
    assert "names no colour" in result.summary


def test_a_mix_of_colours_is_not_applicable(make_offer):
    result = check_photo(make_offer(color="Walnut/White"), clip=FakeClip({}))
    assert result.status == "not_applicable"
    assert "'Walnut/White' isn't one clear colour" in result.summary


def test_a_missing_photo_cannot_be_checked(make_offer, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert check_photo(make_offer(), clip=FakeClip({})).status == "cannot_check"


def test_a_matching_photo_passes(photo):
    clip = FakeClip(spread(grey=0.7))
    result = check_photo(photo(color="Polished Nickel"), clip=clip)
    assert result.status == "ok"
    assert clip.asked[0][1] == "lamp"
    assert result.facts["decided_by"] == "clip"
    assert result.summary.endswith("(grey). CLIP rates the photo 70% grey.")


def test_a_clear_mismatch_is_a_problem_with_its_evidence(photo):
    scores = spread(red=0.95, grey=0.004, black=0.004, white=0.004, beige=0.004, blue=0.004)
    result = check_photo(photo(color="Polished Nickel"), clip=FakeClip(scores))
    assert result.status == "problem"
    [finding] = result.findings
    assert finding.code == "photo_colour_mismatch"
    assert finding.facts == {"listing_colour": "grey", "photo_colour": "red"}
    assert finding.message.startswith(
        "The photo shows a red lamp, but the listing's colour is 'Polished Nickel' (grey)."
    )
    assert "95% red and under 1% grey" in finding.message
    assert list(result.facts["clip_scores"])[0] == "red"
    assert len(result.facts["clip_scores"]) == 3
    assert result.law == ["UCPD Art. 6(1)"]


def test_an_unclear_photo_goes_to_the_reviewer_without_a_vision_model(photo):
    result = check_photo(photo(color="Cream"), clip=FakeClip(spread(beige=0.97, white=0.001)))
    assert result.status == "cannot_check"
    assert result.summary.startswith("CLIP can't tell whether the photo is white")


def test_an_unclear_photo_goes_to_the_vision_model_when_there_is_one(photo):
    vision = FakeVision(
        PhotoAnswer(colour="grey", matches_listing=False, confidence="high", reason="A grey sofa.")
    )
    clip = FakeClip(spread(beige=0.97, white=0.001))
    result = check_photo(photo(color="Cream"), clip=clip, vision=vision)
    assert vision.asked[0][1:] == ("lamp", "Cream")
    assert result.status == "problem"
    assert result.facts["decided_by"] == "vision"
    assert result.findings[0].facts == {"listing_colour": "white", "photo_colour": "grey"}


def test_clear_photos_never_reach_the_vision_model(photo):
    vision = FakeVision(
        PhotoAnswer(colour="red", matches_listing=False, confidence="high", reason="Red.")
    )
    check_photo(photo(color="Polished Nickel"), clip=FakeClip(spread(grey=0.7)), vision=vision)
    assert vision.asked == []


@pytest.mark.parametrize(
    "matches, confidence, status",
    [(True, "low", "ok"), (False, "high", "problem"), (False, "medium", "cannot_check")],
)
def test_only_a_confident_no_from_the_vision_model_is_a_problem(photo, matches, confidence, status):
    answer = PhotoAnswer(
        colour="beige", matches_listing=matches, confidence=confidence, reason="A beige lamp."
    )
    clip = FakeClip(spread(beige=0.97, white=0.001))
    result = check_photo(photo(color="Cream"), clip=clip, vision=FakeVision(answer))
    assert result.status == status
