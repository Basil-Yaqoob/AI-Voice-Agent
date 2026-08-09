"""Embedding providers.

**Local ONNX, not a hosted API** (DECISION 10). Two reasons, in order:

1. *Latency.* Embeddings sit on the speculative-prefetch path, the one path in
   the system deliberately engineered to ~0ms. A hosted embedding call puts a
   network round trip inside it and defeats the mechanism.
2. *Credentials.* There is no embedding key in this project and no clean way to
   get one — OpenRouter serves no ``/embeddings`` endpoint, and the Azure key is
   for Speech, not Azure OpenAI.

The cost is a model download and CPU at inference. ``fastembed`` runs ONNX, so
there is no torch dependency.

:class:`HashEmbedder` remains for tests: deterministic, dependency-free, and
semantically meaningless. It must never be used in production.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any, Literal

from agent.telemetry.logging import get_logger

__all__ = [
    "DEFAULT_DIMENSIONS",
    "DEFAULT_MODEL",
    "FastEmbedProvider",
    "HashEmbedder",
]

log = get_logger(__name__)

# intfloat/multilingual-e5-large: 1024 dims, ~2.24GB.
#
# Chosen on measurement, not size. Against paraphrase-multilingual-MiniLM-L12-v2
# (384 dims, 0.22GB) on four Urdu retrieval cases, 9 Aug 2026:
#
#   case              MiniLM margin   e5-large margin
#   urdu paraphrase       +0.313          +0.053
#   cross-script          +0.783          +0.161
#   code-switched         -0.029  FAIL    +0.026  pass
#   product name          +0.677          +0.074
#
# MiniLM discriminates harder where it works but *fails the code-switched
# query* — the defining scenario for this product. e5-large ranks all four
# correctly. Its margins are narrow because E5 compresses the similarity space;
# ranking order is what matters, not absolute values.
#
# The 2.24GB is a real per-container cost. Accepted because retrieval that
# cannot handle "میرا order کہاں ہے" is not worth deploying.
DEFAULT_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_DIMENSIONS = 1024

# E5 is asymmetric: trained with these exact prefixes. Dropping them is not a
# style choice, it measurably degrades ranking.
_E5_PREFIXES = {"query": "query: ", "passage": "passage: "}


class FastEmbedProvider:
    """Local ONNX embeddings via ``fastembed``.

    The model is loaded lazily on first use and cached on the instance: loading
    takes seconds and must not happen mid-call. Call :meth:`warmup` at worker
    startup so the first caller does not pay for it.
    """

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        cache_dir: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._dimensions = dimensions
        self._cache_dir = cache_dir
        self._model: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return f"fastembed:{self._model_name}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def warmup(self) -> None:
        """Load the model and run one embed, off the critical path.

        e5-large is 2.24GB; the first load takes seconds and must not happen
        while a caller is waiting.
        """
        await self.embed(["warmup"])

    def _apply_prefix(self, texts: Sequence[str], kind: str) -> list[str]:
        if "e5" not in self._model_name.lower():
            return list(texts)
        prefix = _E5_PREFIXES.get(kind, _E5_PREFIXES["passage"])
        return [f"{prefix}{t}" for t in texts]

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:  # re-check inside the lock
                from fastembed import TextEmbedding

                loop = asyncio.get_running_loop()
                self._model = await loop.run_in_executor(
                    None,
                    lambda: TextEmbedding(model_name=self._model_name, cache_dir=self._cache_dir),
                )
                log.info("embedding_model_loaded", model=self._model_name)
        return self._model

    async def embed(
        self, texts: Sequence[str], *, kind: Literal["query", "passage"] = "passage"
    ) -> list[list[float]]:
        if not texts:
            return []

        model = await self._ensure_model()
        prepared = self._apply_prefix(texts, kind)
        loop = asyncio.get_running_loop()

        # fastembed is synchronous and CPU-bound. Running it directly on the
        # event loop would stall the audio frame pump — the same trap as the
        # Azure Speech SDK in TASK-05.
        vectors = await loop.run_in_executor(
            None, lambda: [v.tolist() for v in model.embed(prepared)]
        )

        if vectors and len(vectors[0]) != self._dimensions:
            raise ValueError(
                f"{self._model_name} returned {len(vectors[0])} dimensions, "
                f"but the store schema expects {self._dimensions}. "
                "Set EMBEDDING_DIMENSIONS to match and re-ingest."
            )
        return vectors


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

    async def embed(
        self, texts: Sequence[str], *, kind: Literal["query", "passage"] = "passage"
    ) -> list[list[float]]:
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
