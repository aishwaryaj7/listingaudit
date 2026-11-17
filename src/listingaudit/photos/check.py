"""The photo check: does the product in the photo have the colour the listing names?

CLIP decides the clear cases. A photo it can't settle goes to a vision language
model when one is set up, and otherwise to the reviewer. Only the listing's
colour field is used: the title and description are seller text and play no part.

    uv run python -m listingaudit.photos.check P0077
    uv run python -m listingaudit.photos.check P0077 --vision
"""

import argparse
from dataclasses import dataclass
from typing import Literal

from listingaudit.checks.results import (
    CheckResult,
    Finding,
    cannot_check,
    not_applicable,
    ok,
    problem,
)
from listingaudit.market.models import Offer
from listingaudit.market.rules import Rule
from listingaudit.photos.clip import Clip, get_clip
from listingaudit.photos.colours import family_of, is_near, noun
from listingaudit.photos.store import photo_path
from listingaudit.photos.vision import Vision, VisionReply

RULE = Rule.PHOTO_MATCHES_TEXT

# CLIP's score for the listing's colour decides. From MATCH_MIN on, the colour counts as
# present; below MISMATCH_MAX, with a colour that isn't a near one on top, the photo is
# a mismatch. Both were picked on spare photos (python -m listingaudit.photos.tune).
MATCH_MIN = 0.15
MISMATCH_MAX = 0.01

Outcome = Literal["match", "mismatch", "unclear"]


@dataclass
class Verdict:
    outcome: Outcome
    listing_colour: str  # the family the listing's colour belongs to
    photo_colour: str  # the family CLIP rates highest
    scores: dict[str, float]

    @property
    def listing_score(self) -> float:
        return self.scores[self.listing_colour]


def judge(
    scores: dict[str, float],
    listing_colour: str,
    match_min: float = MATCH_MIN,
    mismatch_max: float = MISMATCH_MAX,
) -> Verdict:
    """Match, mismatch or unclear, from CLIP's colour scores for the photo."""
    photo_colour = max(scores, key=scores.get)
    verdict = Verdict("unclear", listing_colour, photo_colour, scores)
    if photo_colour == listing_colour or verdict.listing_score >= match_min:
        verdict.outcome = "match"
    elif verdict.listing_score < mismatch_max and not is_near(photo_colour, listing_colour):
        verdict.outcome = "mismatch"
    return verdict


def percent(score: float) -> str:
    return f"{score:.0%}" if score >= 0.01 else "under 1%"


def check_photo(
    offer: Offer, clip: Clip | None = None, vision: Vision | None = None
) -> CheckResult:
    """Compare the photo's colour with the listing's colour field."""
    family = family_of(offer.color)
    if family is None:
        reason = (
            "The listing names no colour"
            if not offer.color
            else f"The colour '{offer.color}' isn't one clear colour"
        )
        return not_applicable(RULE, f"{reason}, so there is nothing to compare the photo with.")
    path = photo_path(offer.image_file)
    if not path.exists():
        return cannot_check(RULE, f"The photo {offer.image_file} is missing.")

    thing = noun(offer.product_type)
    verdict = judge((clip or get_clip()).colours(path, thing), family)
    top = sorted(verdict.scores.items(), key=lambda item: item[1], reverse=True)[:3]
    facts = {
        "colour": offer.color,
        "listing_colour": family,
        "photo_colour": verdict.photo_colour,
        "clip_scores": {name: round(score, 3) for name, score in top},
        "decided_by": "clip",
    }
    shown = verdict.photo_colour
    clip_says = f"CLIP rates the photo {percent(verdict.scores[shown])} {shown}"
    if shown != family:
        clip_says += f" and {percent(verdict.listing_score)} {family}"
    clip_says += "."

    if verdict.outcome == "match":
        summary = f"The photo fits the colour '{offer.color}' ({family}). {clip_says}"
        return ok(RULE, summary, **facts)
    if verdict.outcome == "mismatch":
        finding = Finding(
            code="photo_colour_mismatch",
            message=(
                f"The photo shows a {verdict.photo_colour} {thing}, but the listing's colour "
                f"is '{offer.color}' ({family}). {clip_says}"
            ),
            facts={"listing_colour": family, "photo_colour": verdict.photo_colour},
        )
        return problem(RULE, [finding], **facts)
    if vision is None:
        return cannot_check(
            RULE, f"CLIP can't tell whether the photo is {family}: {clip_says}", **facts
        )
    return vision_result(offer, vision.ask(path, thing, offer.color), facts)


def vision_result(offer: Offer, reply: VisionReply, facts: dict) -> CheckResult:
    """The result when the vision model has looked at a photo CLIP couldn't settle."""
    answer = reply.answer
    facts = facts | {
        "decided_by": "vision",
        "photo_colour": answer.colour,
        "vision_answer": answer.model_dump(),
    }
    family = facts["listing_colour"]
    if answer.matches_listing:
        return ok(
            RULE,
            f"The vision model finds the photo fits the colour '{offer.color}': {answer.reason}",
            **facts,
        )
    if answer.confidence == "high":
        finding = Finding(
            code="photo_colour_mismatch",
            message=(
                f"The photo shows a {answer.colour} {noun(offer.product_type)}, but the listing's "
                f"colour is '{offer.color}' ({family}), according to the vision model."
            ),
            facts={"listing_colour": family, "photo_colour": answer.colour},
        )
        return problem(RULE, [finding], **facts)
    return cannot_check(
        RULE,
        f"Neither CLIP nor the vision model is sure the photo is {family}: {answer.reason}",
        **facts,
    )


def main() -> None:
    from listingaudit.checks.run import load_listings
    from listingaudit.db import connect_as_agent

    parser = argparse.ArgumentParser(description="Run the photo check on one listing.")
    parser.add_argument("offer_id", help="e.g. P0077")
    parser.add_argument(
        "--vision", action="store_true", help="ask the vision model when CLIP can't tell"
    )
    args = parser.parse_args()

    with connect_as_agent() as conn:
        listings = load_listings(conn, offer_ids=[args.offer_id])
    if not listings:
        parser.error(f"no listing {args.offer_id}")
    offer, _ = listings[0]
    print(check_photo(offer, vision=Vision() if args.vision else None).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
