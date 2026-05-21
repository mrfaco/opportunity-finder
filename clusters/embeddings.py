"""Text embedding via Voyage AI.

Embeddings are the input to the clustering substrate: each ingested item is
embedded into a 1024-dim vector, and clusters are nearest-centroid groupings
of those vectors. ``voyage-3.5`` outputs 1024 dimensions natively, matching
the pgvector columns in ``clusters.models`` (``EMBEDDING_DIM``).

Lives in the ``clusters`` app because clustering is the only consumer of
embeddings — the filter and the investigation agent do not embed.
"""

from __future__ import annotations

import voyageai
from django.conf import settings

from .models import EMBEDDING_DIM

# Voyage accepts up to 1000 inputs per request; we batch more conservatively
# to stay well under the per-request token ceiling on long items.
_BATCH_SIZE = 128


def get_voyage_client() -> voyageai.Client:
    """Construct a Voyage client. Fails loudly if no key is configured."""
    if not settings.VOYAGE_API_KEY:
        raise RuntimeError(
            "VOYAGE_API_KEY is not set. Configure it in .env before embedding "
            "(see https://www.voyageai.com/ for a key)."
        )
    return voyageai.Client(api_key=settings.VOYAGE_API_KEY)


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Embed a batch of texts into 1024-dim vectors.

    ``input_type`` is ``"document"`` for items being stored and clustered,
    ``"query"`` for a search query — Voyage tunes the two asymmetrically.

    Raises if the model returns a vector whose width does not match the
    pgvector column. That is a misconfiguration (wrong ``EMBEDDING_MODEL``,
    or a model whose ``output_dimension`` was not set to 1024) and we want it
    to fail here, not silently store unusable vectors.
    """
    if not texts:
        return []
    client = get_voyage_client()
    model = settings.EMBEDDING_MODEL
    out: list[list[float]] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        result = client.embed(batch, model=model, input_type=input_type)
        # Coerce to float — voyage-3.5 returns floats, but the SDK return type
        # also admits integer (quantized) embeddings; pgvector wants floats.
        out.extend([float(x) for x in vec] for vec in result.embeddings)
    for vec in out:
        if len(vec) != EMBEDDING_DIM:
            raise RuntimeError(
                f"Embedding model {model!r} returned a {len(vec)}-dim vector; "
                f"the pgvector columns expect {EMBEDDING_DIM}. Check "
                "EMBEDDING_MODEL and the model's output dimensionality."
            )
    return out


def embed_text(text: str, input_type: str = "document") -> list[float]:
    """Embed a single text into a 1024-dim vector."""
    return embed_texts([text], input_type=input_type)[0]
