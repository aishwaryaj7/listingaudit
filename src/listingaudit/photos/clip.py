"""Zero-shot colour recognition with CLIP (OpenAI's ViT-B/32 by default).

CLIP maps photos and sentences into the same vector space, so a photo can be
compared with sentences like "a photo of a red lamp" without any training. Each
colour family is represented by the average of several such sentences (prompt
ensembling), which is steadier than any single wording.
"""

from collections.abc import Sequence
from functools import cache
from pathlib import Path

import numpy as np
import open_clip
import torch
from PIL import Image

from listingaudit.config import settings
from listingaudit.photos.colours import FAMILIES

TEMPLATES = [
    "a photo of {thing}.",
    "a product photo of {thing}.",
    "a close-up photo of {thing}.",
    "{thing}.",
]
# Words used for each family in the sentences
COLOUR_WORDS = {
    "black": ["black"],
    "white": ["white"],
    "grey": ["grey", "gray", "silver"],
    "beige": ["beige", "tan"],
    "brown": ["brown"],
    "blue": ["blue", "navy blue"],
    "green": ["green"],
    "red": ["red"],
    "pink": ["pink"],
    "orange": ["orange"],
    "yellow": ["yellow"],
    "gold": ["gold", "brass"],
    "purple": ["purple"],
}


def with_article(words: str) -> str:
    return f"{'an' if words[0] in 'aeiou' else 'a'} {words}"


def square(image: Image.Image) -> Image.Image:
    """Pad to a square with white, so the model's centre crop keeps the whole product."""
    side = max(image.size)
    canvas = Image.new("RGB", (side, side), "white")
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    return canvas


class Clip:
    def __init__(self, model_name: str | None = None, pretrained: str | None = None):
        model_name = model_name or settings.clip_model
        # CPU (the default) on purpose: GPU and MPS results differ slightly between machines
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained or settings.clip_pretrained,
            cache_dir=str(settings.models_dir),
        )
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.logit_scale = self.model.logit_scale.detach().exp().item()
        self._families: dict[str, np.ndarray] = {}

    @torch.no_grad()
    def encode_images(self, paths: Sequence[Path]) -> np.ndarray:
        """Unit-length image vectors, one row per photo."""
        batch = torch.stack(
            [self.preprocess(square(Image.open(path).convert("RGB"))) for path in paths]
        )
        features = self.model.encode_image(batch)
        return (features / features.norm(dim=-1, keepdim=True)).numpy()

    @torch.no_grad()
    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        features = self.model.encode_text(self.tokenizer(list(texts)))
        return (features / features.norm(dim=-1, keepdim=True)).numpy()

    def family_vectors(self, noun: str) -> np.ndarray:
        """One unit vector per colour family, for photos of this kind of product."""
        if noun not in self._families:
            rows = []
            for family in FAMILIES:
                sentences = [
                    template.format(thing=with_article(f"{word} {noun}"))
                    for word in COLOUR_WORDS[family]
                    for template in TEMPLATES
                ]
                mean = self.encode_texts(sentences).mean(axis=0)
                rows.append(mean / np.linalg.norm(mean))
            self._families[noun] = np.stack(rows)
        return self._families[noun]

    def colour_scores(self, image_vector: np.ndarray, noun: str) -> dict[str, float]:
        """How likely each colour family is for the product in the photo (they add up to 1)."""
        logits = self.logit_scale * self.family_vectors(noun) @ image_vector
        weights = np.exp(logits - logits.max())
        return dict(zip(FAMILIES, (weights / weights.sum()).tolist(), strict=True))

    def colours(self, path: Path, noun: str) -> dict[str, float]:
        return self.colour_scores(self.encode_images([path])[0], noun)


@cache
def get_clip() -> Clip:
    """Shared model; loading it takes a few seconds."""
    return Clip()
