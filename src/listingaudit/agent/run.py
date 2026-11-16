"""Audit listings with the agent, and print each report.

    uv run python -m listingaudit.agent.run P0171 P0198 --save

The language models need VISION_API_KEY in .env (see README). Each step is printed as
it ends, then the report, the time it took and the tokens the models used. With
--save, each audit is also written as JSON to data/results/agent/.
"""

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler

from listingaudit.agent.brain import Brain
from listingaudit.agent.graph import build_graph, counted, found_passages
from listingaudit.agent.report import Report, render
from listingaudit.agent.toolbox import ToolError, open_toolbox
from listingaudit.config import settings

# List prices in US dollars per million input and output tokens (November 2026)
PRICES = {"gpt-5-mini": (0.25, 2.00)}


def cost(usage: dict[str, Any]) -> float | None:
    """What the tokens cost, or None for a model without a known price."""
    total = 0.0
    for model, tokens in usage.items():
        price = next((p for name, p in PRICES.items() if model.startswith(name)), None)
        if price is None:
            return None
        total += (tokens["input_tokens"] * price[0] + tokens["output_tokens"] * price[1]) / 1e6
    return total


def step_line(step: str, update: dict[str, Any]) -> str:
    """One line on what a step of the graph did."""
    match step:
        case "read_listing":
            return update["listing"]["title"][:70]
        case "pick_rules":
            rules = ", ".join(rule["rule"] for rule in update["rules"])
            return f"{update['kind']} ({update['kind_reason']}) -> {rules}"
        case "run_checks":
            statuses = [result["status"] for result in update["results"]]
            found = counted(statuses.count("problem"), "possible problem")
            return f"{counted(len(statuses), 'check')}, {found}"
        case "look_up_law":
            calls = update["messages"][-1].tool_calls
            return "; ".join(call["args"].get("query", "") for call in calls) or "done searching"
        case "search":
            return f"{len(found_passages(update['messages']))} passages"
        case "self_check":
            return counted(len(update["feedback"]), "issue") if update["feedback"] else "passed"
        case "write_report":
            return f"draft {update['attempts']}"
        case "escalate":
            return "the large model takes over"
    return "report ready for review"


async def audit(graph: Any, offer_id: str, save: bool) -> None:
    usage = UsageMetadataCallbackHandler()
    started = last = time.perf_counter()
    state: dict[str, Any] = {"offer_id": offer_id}
    # "updates" gives what each step changed, "values" the whole state after it
    modes = ["updates", "values"]
    async for mode, chunk in graph.astream(state, {"callbacks": [usage]}, stream_mode=modes):
        if mode == "values":
            state = chunk
            continue
        for step, update in chunk.items():
            now = time.perf_counter()
            print(f"  {step} ({now - last:.1f} s): {step_line(step, update)}", file=sys.stderr)
            last = now

    seconds = time.perf_counter() - started
    report = Report.model_validate(state["report"])
    tokens = {
        model: {key: data[key] for key in ("input_tokens", "output_tokens")}
        for model, data in usage.usage_metadata.items()
    }
    price = cost(tokens)
    print(render(report))
    used = ", ".join(
        f"{model}: {data['input_tokens']} in, {data['output_tokens']} out"
        for model, data in tokens.items()
    )
    print(f"{seconds:.0f} s; tokens: {used or 'none'}" + (f"; about ${price:.4f}" if price else ""))

    if save:
        folder = settings.data_dir / "results" / "agent"
        folder.mkdir(parents=True, exist_ok=True)
        searches = [c["args"] for m in state["messages"] for c in getattr(m, "tool_calls", [])]
        record = {
            "report": state["report"],
            "kind_reason": state["kind_reason"],
            "searches": searches,
            "drafts": state.get("attempts", 0),
            "large": state.get("large", False),
            "seconds": round(seconds, 1),
            "tokens": tokens,
        }
        path = folder / f"{offer_id}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved to {path}", file=sys.stderr)


async def main_async(offer_ids: list[str], save: bool) -> None:
    brain = Brain.from_settings()
    async with open_toolbox() as toolbox:
        graph = build_graph(toolbox, brain)
        for offer_id in offer_ids:
            print(f"Auditing {offer_id}", file=sys.stderr)
            try:
                await audit(graph, offer_id, save)
            except ToolError as error:
                print(f"  {error}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit listings with the agent.")
    parser.add_argument("offer_ids", nargs="+", help="e.g. P0171")
    parser.add_argument(
        "--save", action="store_true", help="write each audit to data/results/agent/"
    )
    args = parser.parse_args()
    if settings.vision_api_key is None:
        parser.error("No language model is set up: add VISION_API_KEY to .env (see README).")
    asyncio.run(main_async(args.offer_ids, args.save))


if __name__ == "__main__":
    main()
