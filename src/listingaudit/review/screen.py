"""The review screen: read each report beside its listing, and approve or reject it.

    uv run streamlit run src/listingaudit/review/screen.py

The screen is a client of the review API, which has to be running too (API_URL, by
default http://127.0.0.1:8000). Saving a decision saves the review and a draft message
to the seller. Nothing on this screen contacts a seller.

Text written by sellers, the model or reviewers is shown with st.text, never as
Markdown, so it appears exactly as written.
"""

from datetime import datetime
from typing import Any

import httpx
import streamlit as st

from listingaudit.agent.report import Problem, Report, counted, short
from listingaudit.config import settings
from listingaudit.review.client import ApiError, ReviewApi

SEVERITY = {"high": "red", "medium": "orange"}
PRICE_KINDS = {"previous_price": "the seller's earlier price", "rrp": "recommended retail price"}

st.set_page_config(
    page_title="ListingAudit review", page_icon=":material/fact_check:", layout="wide"
)

if "api" not in st.session_state:
    st.session_state.api = ReviewApi(settings.api_url)
api: ReviewApi = st.session_state.api


def select(audit_id: str | None) -> None:
    st.session_state.selected = audit_id


def run_audit(offer_id: str) -> None:
    """Audit a listing, showing each step as it ends."""
    with st.status(f"Auditing {offer_id}", expanded=True) as status:
        try:
            for event, data in api.start(offer_id):
                if event == "error":
                    status.update(label=f"The audit of {offer_id} failed", state="error")
                    st.error(data["detail"])
                    return
                if data["step"] == "waiting":
                    select(data["audit_id"])
                    label = f"{offer_id} is ready for review, after {data['seconds']:.1f} s"
                    status.update(label=label, state="complete", expanded=False)
                else:
                    st.text(f"{data['step']} ({data['seconds']:.1f} s): {data['detail']}")
        except ApiError as error:
            status.update(label=f"{offer_id} can't be audited", state="error")
            st.error(" ".join(error.errors))


def show_listing(listing: dict[str, Any], photo: bytes) -> None:
    st.image(photo, width="stretch")
    price, was = st.columns(2)
    price.metric("Price", f"€{listing['price']}")
    if listing["was_price"]:
        was.metric(
            "Crossed-out price",
            f"€{listing['was_price']}",
            help=f"Shown as {PRICE_KINDS[listing['was_price_kind']]}",
        )
    if listing["discount_percent"]:
        st.badge(f"Badge: -{listing['discount_percent']}%", color="red")
    maker = [listing["manufacturer_name"], listing["manufacturer_address"]]
    eu_person = [listing["eu_rp_name"], listing["eu_rp_address"]]
    details = {
        "Colour": listing["color"],
        "Net quantity": listing["net_quantity"],
        "Unit price": listing["unit_price"]
        and f"€{listing['unit_price']} per {listing['unit_price_per']}",
        "Manufacturer": ", ".join(filter(None, maker)),
        "Responsible person in the EU": ", ".join(filter(None, eu_person)),
        "Fibre composition": listing["fibre_composition"],
        "Energy class": listing["energy_class"],
        "Safety warnings": listing["safety_warnings"],
    }
    for label, value in details.items():
        if value:
            st.text(f"{label}: {value}")
    with st.expander("Bullet points and description"):
        for point in listing["bullet_points"]:
            st.text(f"• {point}")
        if listing["description"]:
            st.text(listing["description"])


def show_problem(problem: Problem) -> None:
    st.markdown(f"**{problem.name}**")
    st.badge(f"{problem.severity} severity", color=SEVERITY[problem.severity])
    st.text(problem.explanation)
    with st.expander("Evidence and law"):
        for line in problem.evidence:
            st.text(line)
        st.text(f"Law: {'; '.join(problem.law)}")
        for quote in problem.quotes:
            st.link_button(quote.citation, quote.url, icon=":material/gavel:")
            st.text(short(quote.text, 600))


def review_form(audit_id: str, report: Report) -> None:
    """Approve or reject each possible problem; saving sends the review to the API."""
    with st.form(f"review-{audit_id}", border=False):
        for problem in report.problems:
            key = f"{audit_id}-{problem.rule}"
            with st.container(border=True):
                show_problem(problem)
                st.segmented_control("Decision", ["Approve", "Reject"], key=f"{key}-decision")
                st.text_input(
                    "Why reject it", key=f"{key}-reason", placeholder="Needed to reject it"
                )
                st.text_area(
                    "The fix the seller gets",
                    value=problem.suggested_fix,
                    key=f"{key}-fix",
                    height=90,
                    placeholder="Needed to approve it: the report has no fix",
                )
        if not report.problems:
            st.success("No possible problems. Save to close the audit.")
        st.text_area(
            "Note for the audit log (optional)",
            key=f"{audit_id}-note",
            placeholder="For example, what you saw in the photo",
        )
        saved = st.form_submit_button("Save the decision", type="primary", icon=":material/check:")
    if saved:
        save_review(audit_id, report)


def save_review(audit_id: str, report: Report) -> None:
    reviewer = st.session_state.get("reviewer", "").strip()
    if not reviewer:
        st.error("Write your name in the sidebar first: the audit log records who decided.")
        return
    decisions = []
    for problem in report.problems:
        key = f"{audit_id}-{problem.rule}"
        choice = st.session_state[f"{key}-decision"]
        # A finding left open goes unsent, and the API names what is missing
        if choice:
            decisions.append(
                {
                    "rule": problem.rule,
                    "approved": choice == "Approve",
                    "reason": st.session_state[f"{key}-reason"],
                    "fix": st.session_state[f"{key}-fix"],
                }
            )
    review = {
        "reviewer": reviewer,
        "decisions": decisions,
        "note": st.session_state[f"{audit_id}-note"],
    }
    try:
        api.review(audit_id, review)
    except ApiError as error:
        st.error("The decision isn't complete yet:\n\n" + "\n".join(f"- {e}" for e in error.errors))
        return
    st.rerun()


def show_review(audit: dict[str, Any], report: Report) -> None:
    review = audit["review"]
    decisions = {decision["rule"]: decision for decision in review["decisions"]}
    for problem in report.problems:
        decision = decisions[problem.rule]
        with st.container(border=True):
            show_problem(problem)
            if decision["approved"]:
                st.badge("Approved", icon=":material/check:", color="green")
            else:
                st.badge("Rejected", icon=":material/close:", color="gray")
                st.text(f"Why: {decision['reason']}")
    when = datetime.fromisoformat(audit["reviewed_at"]).strftime("%d %b %Y at %H:%M UTC")
    st.text(f"Decided by {review['reviewer']} on {when}.")
    if review["note"]:
        st.text(f"Note: {review['note']}")

    st.markdown("##### Draft message to the seller")
    message = audit["message"]
    if message is None:
        st.info("No finding was approved, so there is no message to the seller.")
        return
    st.caption("Saved as a draft for a person to send. Nothing has been sent.")
    st.text(f"To: seller {message['seller_id']}\nSubject: {message['subject']}")
    st.code(message["body"], language=None, wrap_lines=True)


def show_notes(report: Report) -> None:
    if report.to_check:
        with st.expander(f"For a person to check ({len(report.to_check)})", expanded=True):
            for note in report.to_check:
                st.text(f"{note.name}: {note.summary}")
    with st.expander(f"Passed or not needed ({len(report.passed)})"):
        for note in report.passed:
            st.text(f"{note.name}: {note.summary}")


def show_audit(audit_id: str) -> None:
    try:
        audit = api.audit(audit_id)
    except ApiError as error:
        select(None)
        st.warning(" ".join(error.errors))
        return
    report = Report.model_validate(audit["report"])
    status = "waiting for review" if audit["status"] == "waiting" else "reviewed"
    st.subheader(f"{report.offer_id}, {status}")
    st.text(report.title)

    listing_column, report_column = st.columns([2, 3], gap="large")
    with listing_column:
        st.markdown("##### The listing")
        show_listing(api.listing(report.offer_id), api.photo(report.offer_id))
    with report_column:
        st.markdown("##### The report")
        st.text(report.summary)
        if report.unresolved:
            st.warning(
                "The model's draft failed the self-check, so the findings are in the checks' "
                "own words."
            )
        if audit["status"] == "waiting":
            review_form(audit_id, report)
        else:
            show_review(audit, report)
        show_notes(report)


def show_queue(waiting: list[dict[str, Any]]) -> None:
    st.subheader(f"Waiting for review ({len(waiting)})")
    if not waiting:
        st.caption("Nothing is waiting.")
    for audit in waiting:
        found = counted(len(audit["report"]["problems"]), "possible problem")
        st.button(
            f"{audit['offer_id']}: {found}",
            key=f"open-{audit['audit_id']}",
            on_click=select,
            args=(audit["audit_id"],),
            type="primary"
            if audit["audit_id"] == st.session_state.get("selected")
            else "secondary",
            width="stretch",
        )


def show_log() -> None:
    rows = [
        {
            "When": datetime.fromisoformat(line["at"]),
            "Listing": line["offer_id"],
            "Event": line["event"],
            "What happened": line["detail"],
            "Audit": line["audit_id"],
        }
        for line in api.log()
    ]
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={"When": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm")},
    )


try:
    waiting = api.waiting()
except httpx.TransportError:
    st.error(
        f"The review API isn't running at {settings.api_url}. Start it in another terminal "
        "with: uv run uvicorn listingaudit.review.api:app"
    )
    st.stop()

with st.sidebar:
    st.title("ListingAudit")
    st.caption("Reports wait here until a person decides. Nothing is ever sent to a seller.")
    st.text_input("Your name", key="reviewer", help="The audit log records who decided.")
    with st.form("audit", clear_on_submit=True):
        offer_id = st.text_input("Audit a listing", placeholder="P0171")
        start = st.form_submit_button("Start the audit", icon=":material/play_arrow:")

review_tab, log_tab = st.tabs(["Review", "Audit log"])
with review_tab:
    if start and offer_id.strip():
        run_audit(offer_id.strip().upper())
        waiting = api.waiting()
    if st.session_state.get("selected"):
        show_audit(st.session_state.selected)
    else:
        st.info("Choose a report waiting for review on the left, or audit a listing.")
with log_tab:
    show_log()
with st.sidebar:
    show_queue(waiting)
