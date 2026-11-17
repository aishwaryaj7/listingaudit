"""The review screen, run by Streamlit's test harness against a stand-in for the API."""

import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PIL import Image
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from listingaudit.agent.report import Note, Problem, Report, Review, seller_message
from listingaudit.config import PROJECT_ROOT, settings
from listingaudit.market.rules import Rule
from listingaudit.review.client import ApiError

SCREEN = PROJECT_ROOT / "src" / "listingaudit" / "review" / "screen.py"
AUDIT_ID = "0b7a9a8e-1f3c-4d2e-9a6b-5c4d3e2f1a0b"
EXPLANATION = "The badge says -42%, but the price is only 25.0% below €19.99."
REPORT = Report(
    offer_id="P0171",
    title="Throw pillow cover, 45 x 45 cm",
    kind="textile",
    summary="One possible problem: the -42% badge.",
    problems=[
        Problem(
            rule=Rule.PRIOR_PRICE,
            name="Honest discounts (the 30-day rule)",
            severity="high",
            explanation=EXPLANATION,
            evidence=["The badge says -42%, but the lowest price was €19.99."],
            facts={"badge_percent": 42},
            law=["Directive 98/6/EC Art. 6a(1)"],
            quotes=[],
            suggested_fix="Show -25% on the badge, or leave the badge out.",
        )
    ],
    to_check=[
        Note(
            rule=Rule.PHOTO_MATCHES_TEXT,
            name="Photo matches the colour",
            status="cannot_check",
            summary="A person has to look at the photo.",
        )
    ],
    passed=[],
    unresolved=[],
)


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "navy").save(buffer, "JPEG")
    return buffer.getvalue()


class FakeApi:
    """Stands in for the review API: one audit waiting, and the calls it gets."""

    def __init__(self, listing: dict) -> None:
        self.listing_data = listing
        self.audits = {
            AUDIT_ID: {
                "audit_id": AUDIT_ID,
                "offer_id": "P0171",
                "status": "waiting",
                "reported_at": "2026-11-16T20:00:00+00:00",
                "report": REPORT.model_dump(mode="json"),
                "review": None,
                "reviewed_at": None,
                "flagged": False,
                "message": None,
            }
        }
        self.reviews: list[dict] = []
        self.started: list[str] = []

    def waiting(self):
        return [audit for audit in self.audits.values() if audit["status"] == "waiting"]

    def audit(self, audit_id):
        return self.audits[audit_id]

    def listing(self, offer_id):
        return self.listing_data

    def photo(self, offer_id):
        return jpeg()

    def log(self):
        return [
            {
                "at": "2026-11-16T20:00:00+00:00",
                "audit_id": AUDIT_ID,
                "offer_id": "P0171",
                "event": "reported",
                "detail": REPORT.summary,
            }
        ]

    def start(self, offer_id):
        self.started.append(offer_id)
        yield (
            "message",
            {"audit_id": AUDIT_ID, "step": "read_listing", "detail": "Pillow", "seconds": 0.1},
        )
        yield (
            "message",
            {"audit_id": AUDIT_ID, "step": "waiting", "detail": "Ready.", "seconds": 9.5},
        )

    def review(self, audit_id, review):
        self.reviews.append(review)
        if len(review["decisions"]) < len(REPORT.problems):
            raise ApiError(["Approve or reject the finding on Honest discounts (the 30-day rule)."])
        checked = Review.model_validate(review)
        message = seller_message(REPORT, checked, seller_id=7)
        self.audits[audit_id] |= {
            "status": "reviewed",
            "review": checked.model_dump(mode="json"),
            "reviewed_at": datetime(2026, 11, 16, 20, 5, tzinfo=UTC).isoformat(),
            "flagged": True,
            "message": message.model_dump(mode="json"),
        }
        return self.audits[audit_id]


@pytest.fixture
def fake(make_offer):
    listing = make_offer(
        offer_id="P0171",
        price=Decimal("14.99"),
        was_price=Decimal("25.99"),
        was_price_kind="previous_price",
        discount_percent=42,
    )
    return FakeApi(listing.model_dump(mode="json"))


def screen(api: FakeApi) -> AppTest:
    app = AppTest.from_file(str(SCREEN), default_timeout=30)
    app.session_state["api"] = api
    return app.run()


def open_audit(app: AppTest) -> AppTest:
    return app.button(key=f"open-{AUDIT_ID}").click().run()


def save(app: AppTest) -> AppTest:
    [button] = [button for button in app.button if button.label == "Save the decision"]
    return button.click().run()


def test_the_report_is_shown_beside_the_listing_and_its_photo(fake):
    app = open_audit(screen(fake))

    assert not app.exception
    assert app.subheader[0].value == "P0171, waiting for review"
    assert len(app.get("image")) == 1
    assert [metric.value for metric in app.metric] == ["€14.99", "€25.99"]
    assert EXPLANATION in [text.value for text in app.text]
    decision = app.segmented_control(key=f"{AUDIT_ID}-prior_price-decision")
    assert decision.options == ["Approve", "Reject"]
    assert decision.value is None
    fix = app.text_area(key=f"{AUDIT_ID}-prior_price-fix")
    assert fix.value == "Show -25% on the badge, or leave the badge out."


def test_saving_a_decision_sends_one_decision_per_finding(fake):
    app = screen(fake)
    app.text_input(key="reviewer").input("Test Reviewer")
    app = open_audit(app)
    app.segmented_control(key=f"{AUDIT_ID}-prior_price-decision").set_value("Approve")
    app.text_area(key=f"{AUDIT_ID}-prior_price-fix").input("Show -25% on the badge.")
    app = save(app)

    assert fake.reviews == [
        {
            "reviewer": "Test Reviewer",
            "decisions": [
                {
                    "rule": "prior_price",
                    "approved": True,
                    "reason": "",
                    "fix": "Show -25% on the badge.",
                }
            ],
            "note": "",
        }
    ]
    assert app.subheader[0].value == "P0171, reviewed"
    [message] = app.code
    assert "What to change: Show -25% on the badge." in message.value
    assert any("Nothing has been sent." in caption.value for caption in app.main.caption)
    assert app.sidebar.subheader[0].value == "Waiting for review (0)"


def test_a_decision_the_api_refuses_shows_what_is_missing(fake):
    app = save(open_audit(screen(fake)))
    assert app.error[0].value.startswith("Write your name in the sidebar first")
    assert fake.reviews == []

    app.text_input(key="reviewer").input("Test Reviewer")
    app = save(app)
    assert "Approve or reject the finding on Honest discounts" in app.error[0].value
    assert app.subheader[0].value == "P0171, waiting for review"


def test_an_audit_started_on_the_screen_shows_its_steps_and_opens(fake):
    app = screen(fake)
    [offer] = [field for field in app.text_input if field.label == "Audit a listing"]
    offer.input("p0171")
    [start] = [button for button in app.button if button.label == "Start the audit"]
    app = start.click().run()

    assert fake.started == ["P0171"]
    assert app.status[0].label == "P0171 is ready for review, after 9.5 s"
    assert "read_listing (0.1 s): Pillow" in [text.value for text in app.text]
    assert app.subheader[0].value == "P0171, waiting for review"


def sign_in(app: AppTest, password: str) -> AppTest:
    app.text_input[0].input(password)
    [button] = [button for button in app.button if button.label == "Sign in"]
    return button.click().run()


def test_with_a_password_the_screen_asks_for_it_before_anything_else(fake, monkeypatch):
    monkeypatch.setattr(settings, "screen_password", SecretStr("correct horse battery"))
    app = screen(fake)
    assert not app.exception
    assert [box.label for box in app.text_input] == ["Password"]

    app = sign_in(app, "correct horse")
    assert [error.value for error in app.error] == ["That password isn't right."]
    assert [box.label for box in app.text_input] == ["Password"]

    app = sign_in(app, "correct horse battery")
    assert not app.exception
    assert "Your name" in [box.label for box in app.text_input]
    assert "Password" not in [box.label for box in app.text_input]
