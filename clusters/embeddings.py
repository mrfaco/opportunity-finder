"""Text embedding via Voyage AI.

Embeddings are the input to the clustering substrate: each ingested item is
embedded into a 1024-dim vector, and clusters are nearest-centroid groupings
of those vectors. ``voyage-3.5`` outputs 1024 dimensions natively, matching
the pgvector columns in ``clusters.models`` (``EMBEDDING_DIM``).

Lives in the ``clusters`` app because clustering is the only consumer of
embeddings — the filter and the investigation agent do not embed.
"""

from __future__ import annotations

import logging
import time

import voyageai
import voyageai.error
from django.conf import settings
from voyageai.object.embeddings import EmbeddingsObject

from clusters.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

# Voyage accepts up to 1000 inputs per request; we batch more conservatively
# to stay well under the per-request token ceiling on long items.
_BATCH_SIZE = 128

# Bounded retry budget for transient rate-limit responses. The Voyage SDK's
# own tenacity controller is tuned for normal transients and gives up well
# before the free-tier 3 RPM window reopens; this is the app-level guard.
# Total worst-case wait before the final attempt: 2+4+8+16+32 = 62s.
_MAX_RETRY_ATTEMPTS = 6
_BASE_RETRY_WAIT_S = 2.0
_MAX_RETRY_WAIT_S = 60.0


def get_voyage_client() -> voyageai.Client:
    """Construct a Voyage client. Fails loudly if no key is configured."""
    if not settings.VOYAGE_API_KEY:
        raise RuntimeError(
            "VOYAGE_API_KEY is not set. Configure it in .env before embedding "
            "(see https://www.voyageai.com/ for a key)."
        )
    return voyageai.Client(api_key=settings.VOYAGE_API_KEY)


def _embed_with_retry(
    client: voyageai.Client, batch: list[str], model: str, input_type: str
) -> EmbeddingsObject:
    """Call ``client.embed`` with bounded exponential backoff on RateLimitError.

    Only ``RateLimitError`` is retried — auth, validation, and connection
    errors fail loud on the first attempt. The final attempt is outside the
    try/except so failure propagates with the unmodified traceback.
    """
    for attempt in range(_MAX_RETRY_ATTEMPTS - 1):
        try:
            return client.embed(batch, model=model, input_type=input_type)
        except voyageai.error.RateLimitError:  # allow: suppress-exception (retried below)
            wait = min(_MAX_RETRY_WAIT_S, _BASE_RETRY_WAIT_S * (2**attempt))
            logger.warning(
                "voyage rate-limited; sleeping %.0fs before retry %d/%d",
                wait,
                attempt + 2,
                _MAX_RETRY_ATTEMPTS,
            )
            time.sleep(wait)
    # Final attempt: no except — failure propagates so the pipeline checkpoint
    # stays at the last successfully-embedded item.
    return client.embed(batch, model=model, input_type=input_type)


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
        result = _embed_with_retry(client, batch, model, input_type)
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
