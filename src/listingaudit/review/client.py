"""A client of the review API, as the review screen uses it."""

import json
from collections.abc import Iterator
from typing import Any

import httpx


class ApiError(Exception):
    """The API refused a request; the errors say why."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ".join(errors))
        self.errors = errors


def refusal(response: httpx.Response) -> ApiError:
    detail = response.json().get("detail", response.reason_phrase)
    if isinstance(detail, str):
        return ApiError([detail])
    # FastAPI's own validation errors are objects; the service's are sentences
    return ApiError(
        [item if isinstance(item, str) else f"{item['loc'][-1]}: {item['msg']}" for item in detail]
    )


class ReviewApi:
    def __init__(self, url: str) -> None:
        # An audit can take a minute; the stream sends a ping every 15 s while it waits
        self.http = httpx.Client(base_url=url, timeout=httpx.Timeout(10, read=60))

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.http.request(method, path, **kwargs)
        if response.is_error:
            raise refusal(response)
        return response

    def waiting(self) -> list[dict[str, Any]]:
        return self.request("GET", "/audits").json()

    def audit(self, audit_id: str) -> dict[str, Any]:
        return self.request("GET", f"/audits/{audit_id}").json()

    def listing(self, offer_id: str) -> dict[str, Any]:
        return self.request("GET", f"/listings/{offer_id}").json()

    def photo(self, offer_id: str) -> bytes:
        return self.request("GET", f"/listings/{offer_id}/photo").content

    def log(self) -> list[dict[str, Any]]:
        return self.request("GET", "/log").json()

    def review(self, audit_id: str, review: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/audits/{audit_id}/review", json=review).json()

    def start(self, offer_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Audit a listing, giving each server-sent event as (event, data) as it comes."""
        with self.http.stream("POST", f"/listings/{offer_id}/audits") as response:
            if response.is_error:
                response.read()
                raise refusal(response)
            event = "message"
            # Each event is an optional "event:" line, a "data:" line and a blank line;
            # lines starting with ":" are pings
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    yield event, json.loads(line.removeprefix("data:"))
                    event = "message"
