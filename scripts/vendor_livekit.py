"""Vendor the LiveKit browser client.

Kept local rather than loaded from a CDN. Conference wifi blocks CDNs, and a
demo that depends on one is a demo that fails in the room where it matters.

    uv run python scripts/vendor_livekit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

URL = "https://cdn.jsdelivr.net/npm/livekit-client@2/dist/livekit-client.umd.min.js"
TARGET = Path(__file__).resolve().parents[1] / "src/agent/api/static/vendor/livekit-client.umd.js"

# A truncated download would fail at runtime with a confusing "LivekitClient is
# not defined", so sanity-check the size rather than trusting a 200.
MIN_BYTES = 200_000


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    print(f"fetching {URL}")

    body = httpx.get(URL, follow_redirects=True, timeout=60).raise_for_status().content
    if len(body) < MIN_BYTES:
        print(f"refusing to write {len(body)} bytes; expected at least {MIN_BYTES}")
        return 1

    TARGET.write_bytes(body)
    print(f"wrote {TARGET.relative_to(Path.cwd())} ({len(body):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
