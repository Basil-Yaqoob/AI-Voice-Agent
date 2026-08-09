"""Model comparison for TASK-12.

    uv run python eval/run_eval.py                 # default candidates
    uv run python eval/run_eval.py --models a,b    # specific ones
    uv run python eval/run_eval.py --runs 3        # latency samples per case

Writes a markdown report plus a blind CSV for human scoring. Run it **from the
region the agent will run in** — a benchmark from a dev laptop measures the
laptop's ISP, not production.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import random
import time
from datetime import date
from pathlib import Path

import yaml

from agent.config import Settings
from agent.eval_harness import Case, LatencyReport, hard_checks
from agent.interfaces.llm import CachedPrompt, Done, Message, TextDelta, ToolCall
from agent.language.prompts import build_cached_prefix
from agent.providers.llm_openrouter import OpenRouterProvider

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "out"

DEFAULT_MODELS = [
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-haiku-4.5",
    "openai/gpt-5-mini",
]

# Per-minute cost at 4 turns, from RESEARCH.md. Used to show quality against
# price rather than in isolation.
COST_PKR_PER_MIN = {
    "anthropic/claude-opus-5": 11.42,
    "anthropic/claude-sonnet-5": 6.69,
    "anthropic/claude-haiku-4.5": 2.23,
}


def load_cases() -> tuple[list[Case], str, str]:
    data = yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))
    cases = [Case(**c) for c in data["cases"]]
    return cases, data["corpus"], data["persona"]


async def run_case(provider, system: CachedPrompt, case: Case) -> tuple[str, list[str], float]:
    start = time.perf_counter()
    ttft: float | None = None
    parts: list[str] = []
    tools: list[str] = []

    async for event in provider.stream([Message("user", case.utterance)], system=system):
        if isinstance(event, TextDelta):
            if ttft is None:
                ttft = (time.perf_counter() - start) * 1000
            parts.append(event.text)
        elif isinstance(event, ToolCall):
            tools.append(event.name)
        elif isinstance(event, Done):
            break

    return "".join(parts), tools, ttft if ttft is not None else -1.0


async def evaluate(model: str, cases: list[Case], system: CachedPrompt, runs: int) -> dict:
    settings = Settings()
    provider = OpenRouterProvider(
        api_key=settings.openrouter_api_key.get_secret_value(),
        model=model,
        base_url=settings.openrouter_base_url,
        max_tokens=settings.llm_max_tokens,
    )

    samples: list[float] = []
    rows: list[dict[str, object]] = []

    for case in cases:
        for run in range(runs):
            reply, tools, ttft = await run_case(provider, system, case)
            if ttft > 0:
                samples.append(ttft)
            if run == 0:
                rows.append(
                    {
                        "case": case.id,
                        "category": case.category,
                        "utterance": case.utterance,
                        "reply": reply,
                        "failures": hard_checks(case, reply, tools_called=tools),
                        "ttft_ms": round(ttft),
                    }
                )
            # Spacing avoids rate limiting, which otherwise inflates the tail
            # and produces the wrong conclusion.
            await asyncio.sleep(0.25)
        print(f"  {model:34s} {case.id}")

    return {"model": model, "latency": LatencyReport(model, samples), "rows": rows}


def write_report(results: list[dict], budget_ms: int) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"eval-{date.today().isoformat()}.md"

    lines = [
        f"# Model evaluation — {date.today().isoformat()}",
        "",
        "Run from: **record the region here** — a benchmark from a dev laptop",
        "measures the laptop's ISP, not production.",
        "",
        "| Model | TTFT p50 | p95 | p99 | Gate | Hard-check failures | PKR/min |",
        "|---|---:|---:|---:|---|---:|---:|",
    ]

    for r in results:
        lat: LatencyReport = r["latency"]
        gate = lat.gate_status(budget_ms=budget_ms)
        badge = {"pass": "pass", "fail": "**FAIL**", "insufficient": "n/a"}[gate]
        failures = sum(len(row["failures"]) for row in r["rows"])
        cost = COST_PKR_PER_MIN.get(r["model"])
        lines.append(
            f"| `{r['model']}` | {lat.p50:.0f} | {lat.p95:.0f} | {lat.p99:.0f} | "
            f"{badge} | {failures} | {cost if cost else '—'} |"
        )

    lines += [
        "",
        f"Gate is **p95 <= {budget_ms}ms**, not p50: a model with a good median and a bad",
        "tail fails one call in twenty, which is a bad agent.",
        "",
        "## Hard-check failures",
        "",
    ]
    for r in results:
        failing = [row for row in r["rows"] if row["failures"]]
        lines.append(f"### `{r['model']}` — {len(failing)} cases")
        for row in failing:
            lines.append(f"- **{row['case']}** ({row['category']}): {', '.join(row['failures'])}")
        if not failing:
            lines.append("- none")
        lines.append("")

    lines += [
        "## Recommendation",
        "",
        "_Fill in after reading the blind scoring CSV._ Latency and hard checks are",
        "objective; register quality is not, and an LLM judge is the wrong tool for",
        "the one thing this product is bought for.",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_blind_csv(results: list[dict]) -> tuple[Path, Path]:
    """Responses with model names stripped and rows shuffled.

    Brand bias in quality scoring is real and large, so the reviewer must not
    know which model produced which reply.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    blind = OUT / "blind-scoring.csv"
    key = OUT / "blind-key.csv"

    rows = []
    for r in results:
        for row in r["rows"]:
            rows.append({"model": r["model"], **row})
    random.shuffle(rows)

    with blind.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["row", "utterance", "reply", "register_1_5", "codeswitch_1_5", "notes"])
        for i, row in enumerate(rows):
            w.writerow([i, row["utterance"], row["reply"], "", "", ""])

    with key.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["row", "model", "case"])
        for i, row in enumerate(rows):
            w.writerow([i, row["model"], row["case"]])

    return blind, key


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    settings = Settings()
    cases, corpus, persona = load_cases()
    system = CachedPrompt(
        prefix=build_cached_prefix(company="Acme", persona=persona, corpus=corpus, tools=[])
    )

    print(f"{len(cases)} cases x {args.runs} runs\n")
    results = []
    for model in args.models.split(","):
        results.append(await evaluate(model.strip(), cases, system, args.runs))

    report = write_report(results, settings.budget_ttft_ms)
    blind, key = write_blind_csv(results)
    print(f"\nreport: {report}\nblind:  {blind}\nkey:    {key}")


if __name__ == "__main__":
    asyncio.run(main())
