"""Build the retrieval index.

    uv run python scripts/ingest.py [path]

Composition lives here rather than inside ``agent.knowledge`` so that package
stays free of provider imports — a library that constructs its own vendors
drags every SDK into its dependency graph, which the import-linter contract
forbids.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from agent.config import Settings
from agent.knowledge.ingest import DEFAULT_CORPUS, ingest
from agent.providers.registry import build_embedder


def main() -> int:
    corpus = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CORPUS
    if not corpus.exists():
        print(f"{corpus} does not exist. Put the client's documents there first.")
        return 1

    settings = Settings()
    asyncio.run(
        ingest(
            corpus,
            database_url=settings.database_url,
            embedder=build_embedder(settings),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
