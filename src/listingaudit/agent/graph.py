"""The audit agent: a LangGraph graph that audits one listing with the toolbox.

1. read_listing gets the listing.
2. pick_rules: the model judges the kind of product, and the toolbox names the rules.
3. run_checks: code checks each rule. A listing without a possible problem goes
   straight to its report, as there is nothing to explain.
4. look_up_law: the model asks for rulebook searches, which the search step runs, until
   each problem has its passages or the searches run out.
5. write_report: the model words each finding, and self_check, plain code, tests the
   draft. A failed draft goes back with the feedback; one that still fails goes once to
   the large model (escalate). A draft that never passes is left out, and the report
   keeps to the checks' own words.
6. build_report: code builds the report.
7. With a checkpointer, human_review then pauses the graph until a person approves or
   rejects each possible problem, and draft_message drafts the message to the seller
   from the approved ones. Without one, as for batch runs, the graph ends with the report.
"""

from collections import defaultdict
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from listingaudit.agent.brain import Brain, law_messages
from listingaudit.agent.report import (
    CHECKLIST,
    Note,
    Problem,
    Quote,
    Report,
    ReportDraft,
    Review,
    counted,
    review_errors,
    seller_message,
)
from listingaudit.agent.selfcheck import check_draft
from listingaudit.agent.toolbox import Toolbox
from listingaudit.checks.results import CheckResult

# Rounds of rulebook searches the model may ask for, and drafts the small model may write
MAX_SEARCHES = 3
MAX_ATTEMPTS = 2


class AuditState(TypedDict, total=False):
    offer_id: str
    listing: dict[str, Any]
    kind: str
    kind_reason: str
    rules: list[dict[str, Any]]  # as rules_for_listing names them
    results: list[dict[str, Any]]  # a CheckResult for each rule
    messages: Annotated[list[AnyMessage], add_messages]  # the law lookup, searches included
    draft: dict[str, Any]  # the latest ReportDraft
    feedback: list[str]  # what the self-check found wrong with it
    attempts: int  # drafts written so far
    large: bool  # whether the large model has taken over
    report: dict[str, Any]
    review: dict[str, Any]  # a person's Review of the report
    message: dict[str, Any] | None  # the SellerMessage drafted from it, if any


def problems(state: AuditState) -> list[dict[str, Any]]:
    return [result for result in state["results"] if result["status"] == "problem"]


def found_passages(messages: list[AnyMessage]) -> dict[str, dict[str, Any]]:
    """The rulebook passages the searches returned, by chunk id."""
    passages = {}
    for message in messages:
        if isinstance(message, ToolMessage) and message.artifact:
            for passage in message.artifact["structured_content"]["passages"]:
                passages.setdefault(passage["chunk_id"], passage)
    return passages


def assemble(state: AuditState) -> Report:
    """The report. The draft gives only its wording: code fills in the rest."""
    draft = ReportDraft.model_validate(state["draft"]) if "draft" in state else None
    unresolved = state.get("feedback", []) if draft else []
    if unresolved:
        draft = None
    findings = {}
    for finding in draft.findings if draft else []:
        findings.setdefault(finding.rule, finding)
    passages = found_passages(state.get("messages", []))

    found, to_check, passed = [], [], []
    for result in map(CheckResult.model_validate, state["results"]):
        spec = CHECKLIST.rules[result.rule]
        if result.status != "problem":
            note = Note(
                rule=result.rule, name=spec.name, status=result.status, summary=result.summary
            )
            (to_check if result.status == "cannot_check" else passed).append(note)
            continue
        # A problem without a finding is reported in the check's own words
        finding = findings.get(result.rule)
        chunk_ids = dict.fromkeys(finding.quotes) if finding else []
        quotes = [Quote.model_validate(passages[c]) for c in chunk_ids if c in passages]
        found.append(
            Problem(
                rule=result.rule,
                name=spec.name,
                severity=spec.severity,
                explanation=finding.explanation if finding else result.summary,
                evidence=[f.message for f in result.findings],
                facts=result.facts,
                law=result.law,
                quotes=quotes,
                suggested_fix=finding.suggested_fix if finding else "",
            )
        )

    if draft:
        summary = draft.summary
    elif found:
        names = "; ".join(problem.name for problem in found)
        summary = f"{counted(len(found), 'possible problem')}: {names}."
    else:
        summary = f"No possible problems found in {counted(len(state['results']), 'rule')}"
        summary += f"; {counted(len(to_check), 'rule')} for a person to check." if to_check else "."
    return Report(
        offer_id=state["offer_id"],
        title=state["listing"]["title"],
        kind=state["kind"],
        summary=summary,
        problems=found,
        to_check=to_check,
        passed=passed,
        unresolved=unresolved,
    )


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
        case "human_review":
            review = update["review"]
            approved = sum(decision["approved"] for decision in review["decisions"])
            return f"{review['reviewer']} approved {approved} of {len(review['decisions'])}"
        case "draft_message":
            return "message to the seller drafted" if update["message"] else "nothing to send"
    return "report ready"


def build_graph(
    toolbox: Toolbox,
    brain: Brain,
    max_searches: int = MAX_SEARCHES,
    max_attempts: int = MAX_ATTEMPTS,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """The audit graph. With a checkpointer, it pauses for a person's review."""
    lawyer = brain.lawyer(toolbox.search)

    async def read_listing(state: AuditState) -> AuditState:
        return {"listing": await toolbox.call("get_listing", offer_id=state["offer_id"])}

    async def pick_rules(state: AuditState) -> AuditState:
        choice = await brain.choose_kind(state["listing"])
        found = await toolbox.call(
            "rules_for_listing", offer_id=state["offer_id"], kind=choice.kind
        )
        return {"kind": choice.kind, "kind_reason": choice.reason, "rules": found["rules"]}

    async def run_checks(state: AuditState) -> AuditState:
        rules = defaultdict(list)
        for rule in state["rules"]:
            rules[rule["check_with"]].append(rule["rule"])
        results = []
        if rules["run_checks"]:
            found = await toolbox.call(
                "run_checks", offer_id=state["offer_id"], rules=rules["run_checks"]
            )
            results += found["results"]
        if rules["check_photo"]:
            results.append(await toolbox.call("check_photo", offer_id=state["offer_id"]))
        return {"results": results}

    def after_checks(state: AuditState) -> str:
        return "look_up_law" if problems(state) else "build_report"

    async def look_up_law(state: AuditState) -> AuditState:
        # The first turn starts the conversation; the next ones read the search results
        start = [] if state.get("messages") else law_messages(problems(state))
        reply = await lawyer.ainvoke([*state.get("messages", []), *start])
        return {"messages": [*start, reply]}

    def after_law(state: AuditState) -> str:
        messages = state["messages"]
        rounds = sum(1 for m in messages if isinstance(m, AIMessage) and m.tool_calls)
        return "search" if messages[-1].tool_calls and rounds <= max_searches else "write_report"

    async def write_report(state: AuditState) -> AuditState:
        draft = await brain.write_report(
            problems(state),
            found_passages(state["messages"]),
            last_draft=state.get("draft"),
            feedback=state.get("feedback"),
            large=state.get("large", False),
        )
        return {"draft": draft.model_dump(mode="json"), "attempts": state.get("attempts", 0) + 1}

    def self_check(state: AuditState) -> AuditState:
        draft = ReportDraft.model_validate(state["draft"])
        results = [CheckResult.model_validate(result) for result in state["results"]]
        return {"feedback": check_draft(draft, results, found_passages(state["messages"]))}

    def after_self_check(state: AuditState) -> str:
        if not state["feedback"] or state.get("large"):
            return "build_report"
        if state["attempts"] < max_attempts:
            return "write_report"
        return "escalate" if brain.large else "build_report"

    def escalate(state: AuditState) -> AuditState:
        return {"large": True}

    def build_report(state: AuditState) -> AuditState:
        return {"report": assemble(state).model_dump(mode="json")}

    def human_review(state: AuditState) -> AuditState:
        # On each resume the node runs again from the top, and each interrupt() in turn
        # returns the answer it got. An answer that doesn't fit Review is refused before
        # anything is saved; one that leaves a finding open is asked again, with reasons.
        report = Report.model_validate(state["report"])
        question = {"decide_on": [problem.rule.value for problem in report.problems]}
        errors: list[str] = []
        while True:
            review = interrupt(question | {"errors": errors}, response_schema=Review)
            errors = review_errors(report, review)
            if not errors:
                return {"review": review.model_dump(mode="json")}

    def draft_message(state: AuditState) -> AuditState:
        report = Report.model_validate(state["report"])
        review = Review.model_validate(state["review"])
        message = seller_message(report, review, state["listing"]["seller_id"])
        return {"message": message.model_dump(mode="json") if message else None}

    graph = StateGraph(AuditState)
    for step in (read_listing, pick_rules, run_checks, look_up_law, write_report, self_check):
        graph.add_node(step)
    graph.add_node("search", ToolNode([toolbox.search]))
    graph.add_node(escalate)
    graph.add_node(build_report)

    graph.add_edge(START, "read_listing")
    graph.add_edge("read_listing", "pick_rules")
    graph.add_edge("pick_rules", "run_checks")
    graph.add_conditional_edges("run_checks", after_checks, ["look_up_law", "build_report"])
    graph.add_conditional_edges("look_up_law", after_law, ["search", "write_report"])
    graph.add_edge("search", "look_up_law")
    graph.add_edge("write_report", "self_check")
    graph.add_conditional_edges(
        "self_check", after_self_check, ["write_report", "escalate", "build_report"]
    )
    graph.add_edge("escalate", "write_report")
    if checkpointer is None:
        graph.add_edge("build_report", END)
    else:
        graph.add_node(human_review)
        graph.add_node(draft_message)
        graph.add_edge("build_report", "human_review")
        graph.add_edge("human_review", "draft_message")
        graph.add_edge("draft_message", END)
    return graph.compile(checkpointer=checkpointer)
