"""Per-turn cost, in rupees.

The research prices everything in PKR, so the dashboard should too — a figure
you have to convert before you can compare it to a budget does not get checked.

Two deliberate choices:

* **The landed FX rate, not mid-market.** Bank markup plus advance tax on
  foreign card payments makes the real cost meaningfully higher, and a
  dashboard that under-reports is worse than none.
* **Cost per *resolved call*, not per minute.** A verbose agent taking six
  minutes over a two-minute job is a cost problem no vendor discount fixes, and
  per-minute cost hides it completely.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.telemetry.metrics import TurnMetrics

__all__ = ["CallCost", "Rates", "call_cost_pkr", "turn_cost_pkr"]

# Mid-market was 278.6 on 8 Aug 2026. Budget at ~295: bank FX markup runs a few
# percent and advance income tax on foreign card transactions applies on top, at
# a rate that depends on filer status. Confirm with the bank before relying on
# this for anything contractual.
LANDED_PKR_PER_USD = 295.0


@dataclass(frozen=True)
class Rates:
    """Vendor prices in USD. Defaults are list price, 9 Aug 2026."""

    llm_input_per_mtok: float = 5.00  # claude-opus-5
    llm_output_per_mtok: float = 25.00
    llm_cache_read_per_mtok: float = 0.50  # 0.1x of input
    llm_cache_write_per_mtok: float = 6.25  # 1.25x of input

    stt_per_minute: float = 0.0077  # Deepgram Nova-3 streaming
    tts_per_mchar: float = 16.00  # Azure Neural, pay-as-you-go
    media_per_minute: float = 0.0100  # LiveKit agent session
    sip_per_minute: float = 0.0035

    # Planning estimate pending a real carrier quote. PKR 1.50-3.00/min.
    carrier_pkr_per_minute: float = 2.25

    pkr_per_usd: float = LANDED_PKR_PER_USD


@dataclass(frozen=True)
class CallCost:
    llm_pkr: float
    stt_pkr: float
    tts_pkr: float
    media_pkr: float
    carrier_pkr: float

    @property
    def total_pkr(self) -> float:
        return self.llm_pkr + self.stt_pkr + self.tts_pkr + self.media_pkr + self.carrier_pkr


def turn_cost_pkr(metrics: TurnMetrics, rates: Rates | None = None) -> float:
    """Model and TTS cost for one turn. Excludes per-minute lines."""
    r = rates or Rates()
    usd = (
        metrics.input_tokens * r.llm_input_per_mtok
        + metrics.output_tokens * r.llm_output_per_mtok
        + metrics.cache_read_tokens * r.llm_cache_read_per_mtok
        + metrics.cache_write_tokens * r.llm_cache_write_per_mtok
    ) / 1_000_000
    usd += metrics.tts_characters * r.tts_per_mchar / 1_000_000
    return usd * r.pkr_per_usd


def call_cost_pkr(
    turns: list[TurnMetrics],
    *,
    duration_seconds: float,
    rates: Rates | None = None,
) -> CallCost:
    """Full cost of one call, broken down by layer.

    Track this per *resolved* call. A rambling agent that takes three times as
    long shows up here and nowhere else.
    """
    r = rates or Rates()
    minutes = duration_seconds / 60

    llm_usd = sum(
        (
            t.input_tokens * r.llm_input_per_mtok
            + t.output_tokens * r.llm_output_per_mtok
            + t.cache_read_tokens * r.llm_cache_read_per_mtok
            + t.cache_write_tokens * r.llm_cache_write_per_mtok
        )
        / 1_000_000
        for t in turns
    )
    tts_usd = sum(t.tts_characters for t in turns) * r.tts_per_mchar / 1_000_000

    return CallCost(
        llm_pkr=llm_usd * r.pkr_per_usd,
        stt_pkr=minutes * r.stt_per_minute * r.pkr_per_usd,
        tts_pkr=tts_usd * r.pkr_per_usd,
        media_pkr=minutes * (r.media_per_minute + r.sip_per_minute) * r.pkr_per_usd,
        carrier_pkr=minutes * r.carrier_pkr_per_minute,
    )
