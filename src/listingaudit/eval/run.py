"""Run a system on the listings of a split, and keep a record of each audit.

    uv run python -m listingaudit.eval.run agent
    uv run python -m listingaudit.eval.run agent --repeats 5 --sample 10
    uv run python -m listingaudit.eval.run one_prompt
    uv run python -m listingaudit.eval.run rules_only

The exam split is the default: listings nothing was tuned on. Records go to
results/<split>/<system>.jsonl.gz, next to the split's listings and answer key. A run carries on
where it stopped: audits with a record are skipped, and failed ones run again. The
agent and one_prompt call the paid language model; rules_only is free.
"""

import argparse
import asyncio
import gzip
import random
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler

from listingaudit import tracing
from listingaudit.agent.brain import Brain
from listingaudit.agent.graph import build_graph
from listingaudit.agent.report import Report, counted
from listingaudit.agent.run import cost
from listingaudit.agent.toolbox import open_toolbox
from listingaudit.checks.run import CODE_RULES, run_checks
from listingaudit.config import settings
from listingaudit.db import connect
from listingaudit.eval.baselines import OnePrompt, one_prompt_report, rules_only
from listingaudit.eval.records import (
    SYSTEMS,
    Listing,
    Record,
    System,
    read_records,
    records_path,
    save_split,
    source_of,
    write_records,
)
from listingaudit.market.models import Offer, PricePoint

SAMPLE_SEED = 42
# A run stops after this many failed audits in a row, such as when the model's quota is used up
MAX_FAILURES_IN_A_ROW = 5

Audit = Callable[[Offer, list[PricePoint], int], Awaitable[Record]]


def jobs(
    listings: list[Listing], done: set[tuple[str, int]], repeats: int, sample: int | None
) -> list[tuple[Listing, int]]:
    """The audits still to run: each listing (or a fixed sample of them) `repeats` times."""
    if sample:
        picked = set(random.Random(SAMPLE_SEED).sample([o.offer_id for o, _ in listings], sample))
        listings = [listing for listing in listings if listing[0].offer_id in picked]
    return [
        (listing, repeat)
        for listing in listings
        for repeat in range(repeats)
        if (listing[0].offer_id, repeat) not in done
    ]


def used_tokens(usage: UsageMetadataCallbackHandler) -> dict[str, dict[str, int]]:
    return {
        model: {key: data[key] for key in ("input_tokens", "output_tokens")}
        for model, data in usage.usage_metadata.items()
    }


def config(name: str, split: str, usage: Any, handler: Any) -> dict[str, Any]:
    return tracing.run_config(
        usage, handler, name=name, session=f"eval-{split}", tags=["eval", name, split]
    )


def agent_audit(graph: Any, split: str) -> Audit:
    async def audit(offer: Offer, history: list[PricePoint], repeat: int) -> Record:
        usage, handler = UsageMetadataCallbackHandler(), tracing.tracer()
        started = time.perf_counter()
        state = await graph.ainvoke(
            {"offer_id": offer.offer_id}, config("agent", split, usage, handler)
        )
        report = Report.model_validate(state["report"])
        tokens = used_tokens(usage)
        return Record(
            system="agent",
            offer_id=offer.offer_id,
            repeat=repeat,
            kind=state["kind"],
            rules=[rule["rule"] for rule in state["rules"]],
            results=state["results"],
            cited={p.rule: [source_of(q.chunk_id) for q in p.quotes] for p in report.problems},
            report=report,
            seconds=round(time.perf_counter() - started, 1),
            tokens=tokens,
            cost=cost(tokens),
            trace_id=tracing.trace_id(handler),
        )

    return audit


def one_prompt_audit(system: OnePrompt, split: str) -> Audit:
    async def audit(offer: Offer, history: list[PricePoint], repeat: int) -> Record:
        usage, handler = UsageMetadataCallbackHandler(), tracing.tracer()
        started = time.perf_counter()
        answer = await system.audit(offer, history, config("one_prompt", split, usage, handler))
        report, cited = one_prompt_report(offer, answer)
        tokens = used_tokens(usage)
        # The checks' results aren't shown to the model: the grader reads the true numbers there
        results = run_checks(offer, history, CODE_RULES)
        return Record(
            system="one_prompt",
            offer_id=offer.offer_id,
            repeat=repeat,
            results=[result.model_dump(mode="json") for result in results],
            cited=cited,
            report=report,
            seconds=round(time.perf_counter() - started, 1),
            tokens=tokens,
            cost=cost(tokens),
            trace_id=tracing.trace_id(handler),
        )

    return audit


async def rules_only_audit(offer: Offer, history: list[PricePoint], repeat: int) -> Record:
    started = time.perf_counter()
    found = rules_only(offer, history)
    return Record(
        system="rules_only",
        offer_id=offer.offer_id,
        repeat=repeat,
        kind=found.kind,
        rules=found.rules,
        results=[result.model_dump(mode="json") for result in found.results],
        report=found.report,
        seconds=round(time.perf_counter() - started, 1),
        cost=0.0,
    )


def append(path: Path, record: Record) -> None:
    with gzip.open(path, "at", encoding="utf-8") as out:
        out.write(record.model_dump_json() + "\n")


async def run_jobs(
    system: System, todo: list[tuple[Listing, int]], audit: Audit, path: Path, concurrency: int
) -> list[Record]:
    """Run the audits, a few at a time, saving each record as soon as it is done."""
    gate = asyncio.Semaphore(concurrency)
    finished: list[Record] = []
    failures_in_a_row = 0
    stop = asyncio.Event()

    async def one(listing: Listing, repeat: int) -> None:
        nonlocal failures_in_a_row
        offer, history = listing
        async with gate:
            if stop.is_set():
                return
            started = time.perf_counter()
            try:
                record = await audit(offer, history, repeat)
                failures_in_a_row = 0
            except Exception as error:  # a failed audit is kept, and runs again next time
                record = Record(
                    system=system,
                    offer_id=offer.offer_id,
                    repeat=repeat,
                    seconds=round(time.perf_counter() - started, 1),
                    error=f"{type(error).__name__}: {error}"[:500],
                )
                failures_in_a_row += 1
                if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                    stop.set()
        append(path, record)
        finished.append(record)
        found = record.error or counted(len(record.report.problems), "possible problem")
        where = f"{record.offer_id} #{repeat} ({len(finished)}/{len(todo)})"
        print(f"{where} {record.seconds} s: {found}", file=sys.stderr)

    await asyncio.gather(*(one(listing, repeat) for listing, repeat in todo))
    if stop.is_set():
        print(
            f"Stopped after {MAX_FAILURES_IN_A_ROW} failed audits in a row; run the same "
            "command again to carry on.",
            file=sys.stderr,
        )
    return finished


async def main_async(
    system: System,
    split: str,
    limit: int | None,
    repeats: int,
    sample: int | None,
    concurrency: int,
) -> None:
    with connect() as conn:
        listings = save_split(conn, split)
    listings = listings[:limit] if limit else listings

    path = records_path(split, system)
    previous = read_records(path)
    kept_records = [record for record in previous if not record.error]
    write_records(path, kept_records)  # failed audits run again
    todo = jobs(listings, {record.key for record in kept_records}, repeats, sample)
    print(f"{system} on {split}: {len(todo)} audits to run", file=sys.stderr)

    if system == "agent":
        async with open_toolbox() as toolbox:
            graph = build_graph(toolbox, Brain.from_settings())
            finished = await run_jobs(system, todo, agent_audit(graph, split), path, concurrency)
    elif system == "one_prompt":
        audit = one_prompt_audit(OnePrompt.from_settings(), split)
        finished = await run_jobs(system, todo, audit, path, concurrency)
    else:
        finished = await run_jobs(system, todo, rules_only_audit, path, 1)
    tracing.flush()

    done = [record for record in finished if not record.error]
    spent = sum(record.cost or 0 for record in done)
    print(
        f"{len(done)} audits done, {len(finished) - len(done)} failed; about ${spent:.2f}. "
        f"Saved to {path}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a system on a split, for grading.")
    parser.add_argument("system", choices=SYSTEMS)
    parser.add_argument("--split", default="exam", choices=["exam", "practice"])
    parser.add_argument("--limit", type=int, help="only the first N listings, to try a run")
    parser.add_argument(
        "--repeats", type=int, default=1, help="audits of each listing, to test consistency"
    )
    parser.add_argument("--sample", type=int, help="only N listings, picked at random")
    parser.add_argument(
        "--concurrency", type=int, default=4, help="audits at the same time (default 4)"
    )
    args = parser.parse_args()
    if args.system != "rules_only" and settings.vision_api_key is None:
        parser.error("No language model is set up: add VISION_API_KEY to .env (see README).")
    asyncio.run(
        main_async(args.system, args.split, args.limit, args.repeats, args.sample, args.concurrency)
    )


if __name__ == "__main__":
    main()
