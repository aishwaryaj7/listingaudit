"""CLIP colour scores. The tests that run the model need it downloaded to data/models
(python -m listingaudit.photos.evaluate does that); they are skipped otherwise.
"""

import pytest
from huggingface_hub import try_to_load_from_cache
from open_clip import get_pretrained_cfg
from PIL import Image, ImageDraw

from listingaudit.config import settings
from listingaudit.photos.clip import Clip, square, with_article
from listingaudit.photos.colours import FAMILIES


def test_articles_follow_the_sound_of_the_next_word():
    assert with_article("orange lamp") == "an orange lamp"
    assert with_article("red lamp") == "a red lamp"


def test_photos_are_padded_to_a_square_with_white():
    tall = Image.new("RGB", (100, 200), "red")
    padded = square(tall)
    assert padded.size == (200, 200)
    assert padded.getpixel((10, 100)) == (255, 255, 255)
    assert padded.getpixel((100, 100)) == (255, 0, 0)


# --- With the model ------------------------------------------------------------------


@pytest.fixture(scope="module")
def clip():
    repo = get_pretrained_cfg(settings.clip_model, settings.clip_pretrained)["hf_hub"].strip("/")
    weights = try_to_load_from_cache(
        repo, "open_clip_model.safetensors", cache_dir=settings.models_dir
    )
    if not isinstance(weights, str):
        pytest.skip("CLIP is not downloaded yet: run python -m listingaudit.photos.evaluate")
    return Clip()


def cushion(tmp_path, colour: str):
    """A drawing of a cushion in one colour on a white background."""
    image = Image.new("RGB", (224, 224), "white")
    ImageDraw.Draw(image).rounded_rectangle((40, 50, 184, 174), radius=30, fill=colour)
    path = tmp_path / f"{colour}.png"
    image.save(path)
    return path


@pytest.mark.parametrize("colour", ["red", "blue", "green", "yellow", "black"])
def test_clip_names_the_colour_of_a_plain_cushion(clip, tmp_path, colour):
    scores = clip.colours(cushion(tmp_path, colour), "pillow")
    assert max(scores, key=scores.get) == colour


def test_scores_cover_every_family_and_add_up_to_one(clip, tmp_path):
    scores = clip.colours(cushion(tmp_path, "red"), "pillow")
    assert list(scores) == FAMILIES
    assert sum(scores.values()) == pytest.approx(1)


def test_family_vectors_are_kept_for_each_noun(clip):
    first = clip.family_vectors("pillow")
    assert clip.family_vectors("pillow") is first
    assert first.shape[0] == len(FAMILIES)
