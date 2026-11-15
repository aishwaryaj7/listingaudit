"""Score the photo check against the answer key: CLIP, the vision model, and both.

    uv run python -m listingaudit.photos.evaluate
    uv run python -m listingaudit.photos.evaluate --vision

The practice listings that name one clear colour are checked. A planted photo
swap is caught when the check reports a mismatch, and a mismatch on any other
listing is a false alarm. --vision also asks the vision model about every photo
(this costs money); its answers are saved in data/results, so a rerun only asks
about photos it hasn't seen.
"""

import argparse
import json
from pathlib import Path

import pandas as pd

from listingaudit.checks.evaluate import load_answer_key
from listingaudit.checks.run import load_listings
from listingaudit.config import settings
from listingaudit.db import connect
from listingaudit.market.models import Offer, Problem
from listingaudit.photos.check import judge
from listingaudit.photos.clip import Clip, get_clip
from listingaudit.photos.colours import FAMILIES, family_of, noun
from listingaudit.photos.vision import PhotoAnswer, Vision

OUTCOMES = ["caught", "missed", "false alarm", "to reviewer", "correct"]


def answers_file(model: str) -> Path:
    return settings.data_dir / "results" / f"vision_{model}_practice.jsonl"


def checkable(offers: list[Offer]) -> list[Offer]:
    """The listings whose colour field names one clear colour."""
    return [offer for offer in offers if family_of(offer.color)]


def clip_table(offers: list[Offer], problems: list[Problem], clip: Clip) -> pd.DataFrame:
    """One row per checked listing: what was planted, CLIP's scores and its verdict."""
    swaps = {p.offer_id: p for p in problems if p.problem == "photo_mismatch"}
    vectors = clip.encode_images([settings.data_dir / offer.image_file for offer in offers])
    rows = []
    for offer, vector in zip(offers, vectors, strict=True):
        listing_colour = family_of(offer.color)
        swap = swaps.get(offer.offer_id)
        verdict = judge(clip.colour_scores(vector, noun(offer.product_type)), listing_colour)
        rows.append(
            {
                "offer_id": offer.offer_id,
                "product_type": offer.product_type,
                "colour": offer.color,
                "listing_colour": listing_colour,
                # What the photo really shows: the swapped-in product's colour, if any
                "true_colour": family_of(swap.evidence["photo_colour"]) if swap else listing_colour,
                "swapped": swap is not None,
                "difficulty": swap.difficulty if swap else None,
                "photo_colour": verdict.photo_colour,
                "listing_score": verdict.listing_score,
                "clip": verdict.outcome,
                **{f"score_{family}": verdict.scores[family] for family in FAMILIES},
            }
        )
    return pd.DataFrame(rows)


def vision_outcome(answer: PhotoAnswer) -> str:
    """The same three verdicts as CLIP's: only a confident 'doesn't fit' is a mismatch."""
    if answer.matches_listing:
        return "match"
    return "mismatch" if answer.confidence == "high" else "unclear"


def add_vision(table: pd.DataFrame, answers: dict[str, dict]) -> pd.DataFrame:
    """Add the vision model's verdict, and the verdict of CLIP passing unclear photos on."""
    table = table.copy()
    verdicts = {
        offer_id: vision_outcome(PhotoAnswer(**saved["answer"]))
        for offer_id, saved in answers.items()
    }
    table["vision"] = table["offer_id"].map(verdicts)
    table["vision_colour"] = table["offer_id"].map(
        {offer_id: saved["answer"]["colour"] for offer_id, saved in answers.items()}
    )
    table["both"] = table["clip"].where(table["clip"] != "unclear", table["vision"])
    return table


def outcome(swapped: bool, verdict: str) -> str:
    if verdict == "unclear":
        return "to reviewer"
    if swapped:
        return "caught" if verdict == "mismatch" else "missed"
    return "false alarm" if verdict == "mismatch" else "correct"


def scores(table: pd.DataFrame, column: str) -> pd.Series:
    """Counts of each outcome for one way of checking."""
    counts = pd.Series(
        [outcome(s, v) for s, v in zip(table["swapped"], table[column], strict=True)]
    ).value_counts()
    return counts.reindex(OUTCOMES, fill_value=0).rename(column)


def by_difficulty(table: pd.DataFrame, column: str) -> pd.Series:
    """Swaps caught, as 'n of m', for easy and hard swaps."""
    swaps = table[table["swapped"]]
    caught = swaps[column].eq("mismatch").groupby(swaps["difficulty"])
    return (caught.sum().astype(str) + " of " + caught.count().astype(str)).rename(column)


def load_answers(model: str) -> dict[str, dict]:
    path = answers_file(model)
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    return {(saved := json.loads(line))["offer_id"]: saved for line in lines if line}


def ask_vision(offers: list[Offer], vision: Vision) -> dict[str, dict]:
    """Ask the vision model about every photo it hasn't answered yet, saving as it goes."""
    answers = load_answers(vision.model)
    path = answers_file(vision.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        for offer in offers:
            if offer.offer_id in answers:
                continue
            reply = vision.ask(
                settings.data_dir / offer.image_file, noun(offer.product_type), offer.color
            )
            saved = {
                "offer_id": offer.offer_id,
                "model": vision.model,
                "answer": reply.answer.model_dump(),
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
                "seconds": round(reply.seconds, 2),
            }
            out.write(json.dumps(saved) + "\n")
            out.flush()
            answers[offer.offer_id] = saved
            print(f"  {offer.offer_id}: {reply.answer.colour}, fits={reply.answer.matches_listing}")
    return answers


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the photo check on the practice split.")
    parser.add_argument("--vision", action="store_true", help="ask the vision model as well")
    args = parser.parse_args()

    with connect() as conn:
        offers = checkable([offer for offer, _ in load_listings(conn, split="practice")])
        _, problems, _ = load_answer_key(conn, "practice")
    table = clip_table(offers, problems, get_clip())
    columns = ["clip"]

    model = settings.vision_model
    answers = ask_vision(offers, Vision()) if args.vision else load_answers(model)
    asked = sum(offer.offer_id in answers for offer in offers)
    if answers and asked < len(offers):
        print(f"The vision model has seen {asked} of {len(offers)} photos: run with --vision.")
        answers = {}
    if answers:
        table = add_vision(table, answers)
        columns += ["vision", "both"]

    print(f"{len(table)} practice listings name one clear colour; {table['swapped'].sum()} swaps.")
    known = table["true_colour"].notna()
    for column, name in [("photo_colour", "CLIP"), ("vision_colour", "The vision model")]:
        if column in table:
            right = (table[column] == table["true_colour"])[known].mean()
            print(f"{name} names the photo's colour family right for {right:.0%} of them.")
    print()
    print(pd.concat([scores(table, column) for column in columns], axis=1).to_string())
    print("\nSwaps caught:")
    print(pd.concat([by_difficulty(table, column) for column in columns], axis=1).to_string())
    if answers:
        saved = pd.DataFrame(answers.values())
        print(
            f"\nVision model {model}: per 1,000 photos about "
            f"{saved['input_tokens'].mean() * 1000:,.0f} input and "
            f"{saved['output_tokens'].mean() * 1000:,.0f} output tokens, "
            f"{saved['seconds'].mean():.1f} s per photo. With CLIP first, it sees the "
            f"{(table['clip'] == 'unclear').mean():.0%} of photos CLIP can't settle."
        )


if __name__ == "__main__":
    main()
