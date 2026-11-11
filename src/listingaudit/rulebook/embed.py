"""Text embeddings for rulebook search (intfloat/multilingual-e5-small by default).

The model is multilingual, so a German question can match an English article,
and small enough to run on a laptop CPU. E5 models expect a "query: " or
"passage: " prefix on every input; leaving it out noticeably hurts results.
"""

from functools import cache

import numpy as np
from sentence_transformers import SentenceTransformer

from listingaudit.config import settings

DIMENSIONS = 384  # must match VECTOR(384) in db/rulebook.sql


class Embedder:
    def __init__(self, model_name: str | None = None):
        # CPU on purpose: GPU and MPS results differ slightly between machines.
        # The ~540 rulebook chunks take about half a minute.
        self.model = SentenceTransformer(
            model_name or settings.embedding_model,
            cache_folder=str(settings.models_dir),
            device="cpu",
        )
        dimensions = self.model.get_embedding_dimension()
        if dimensions != DIMENSIONS:
            raise ValueError(
                f"model has {dimensions} dimensions, the database expects {DIMENSIONS}"
            )

    def _encode(self, texts: list[str], label: str, progress: bool = False) -> np.ndarray:
        # unit-length vectors, so cosine similarity is a dot product
        return self.model.encode(
            texts,
            prompt=label,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=progress,
        )

    def passages(self, texts: list[str], progress: bool = False) -> np.ndarray:
        return self._encode(texts, "passage: ", progress)

    def query(self, text: str) -> np.ndarray:
        return self._encode([text], "query: ")[0]


@cache
def get_embedder() -> Embedder:
    """Shared embedder; loading the model takes a few seconds."""
    return Embedder()
