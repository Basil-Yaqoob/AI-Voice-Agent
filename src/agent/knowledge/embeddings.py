"""Embedding providers.

⚠️ **The production backend is an open decision.** TASK-07 specifies
``vector(1024)`` but never says which model produces it, and the project has no
embedding credential: OpenRouter serves no ``/embeddings`` endpoint, and the
Azure key is for Speech, not Azure OpenAI. See HANDOFF.md.

The two viable routes:

* **Local ONNX** (``fastembed`` with a multilingual E5 model). No API cost, no
  new credential, and — the architectural argument — no network hop on the
  prefetch path, which is the one path where latency was designed to ~0ms. Costs
  a few hundred MB of model download.
* **A hosted embedding API.** Simplest to add, but puts a network round trip
  inside speculative retrieval and needs a credential that does not exist yet.

Until that is decided, :class:`HashEmbedder` keeps the store, the SQL and the
hybrid ranking fully testable. It is deterministic and dependency-free, and it
must never be used in production — it has no semantic meaning whatsoever.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

__all__ = ["DEFAULT_DIMENSIONS", "HashEmbedder"]

# Matches the small multilingual E5 family, the most likely local choice.
# TASK-07's doc says 1024 (multilingual-e5-large); that model is ~2GB, which is
# a deployment decision rather than a default. Configurable either way.
DEFAULT_DIMENSIONS = 384


class HashEmbedder:
    """Deterministic bag-of-words hashing. **Testing only.**

    Semantically meaningless — two paraphrases share no similarity unless they
    share tokens. It exists so the pgvector schema, the HNSW index and the
    hybrid ranking can be exercised against a real database without committing
    to an embedding model first.
    """

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self._dimensions = dimensions

    @property
    def name(self) -> str:
        return "hash-testing-only"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in re.findall(r"\w+", text.casefold(), flags=re.UNICODE):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]
