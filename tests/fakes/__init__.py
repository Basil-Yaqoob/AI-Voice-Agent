"""Hand-written provider fakes.

Deliberately not ``MagicMock``: these are deterministic, support configurable
delays so latency tests measure orchestration rather than the network, and record
their calls for assertion. Later tasks depend on them, so they live here rather
than beside any one test module.
"""

from __future__ import annotations

from tests.fakes.llm import FakeLLM, LLMCall
from tests.fakes.stt import FakeSTT
from tests.fakes.tts import FakeTTS

__all__ = ["FakeLLM", "FakeSTT", "FakeTTS", "LLMCall"]
