"""Tests for the Events service client."""

from collections.abc import Callable

from fastapi import HTTPException
import httpx
import pytest

from src.domain.events.client import EventsClient


def event_payload(event_id: str = "evt_01hw") -> dict[str, object]:
    return {
        "id": event_id,
        "title": "Inteligência Artificial na Prática",
        "description": "Descrição do evento.",
        "starts_at": "2026-06-15T19:00:00-03:00",
        "ends_at": "2026-06-15T21:00:00-03:00",
        "timezone": "America/Sao_Paulo",
        "registration_deadline": "2026-06-14T23:59:00-03:00",
        "location": {
            "venue": "Auditório PUCRS",
            "address": "Av. Ipiranga, 6681",
            "city": "Porto Alegre",
            "state": "RS",
            "country": "BR",
        },
        "capacity": 200,
        "category": "tecnologia",
        "language": "pt-BR",
        "created_at": "2026-05-01T10:00:00Z",
        "updated_at": "2026-05-10T08:30:00Z",
        "deleted_at": None,
        "deleted_by": None,
        "created_by": "usr_123",
    }


def client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> EventsClient:
    return EventsClient(
        base_url="http://events.test",
        transport=httpx.MockTransport(handler),
        token_provider=lambda **_: "test-token",
    )


def test_list_events_returns_paginated_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/events"
        assert request.url.params["page"] == "2"
        assert request.url.params["limit"] == "10"
        return httpx.Response(
            200,
            json={"data": [event_payload()], "total": 1, "page": 2, "limit": 10},
        )

    result = client_with_handler(handler).list_events(page=2, limit=10)

    assert result.total == 1
    assert result.data[0].id == "evt_01hw"
    assert result.data[0].title == "Inteligência Artificial na Prática"


def test_get_all_events_follows_pagination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        event_id = f"evt_{page}"
        return httpx.Response(
            200,
            json={
                "data": [event_payload(event_id)],
                "total": 2,
                "page": page,
                "limit": 1,
            },
        )

    events = client_with_handler(handler).get_all_events(limit=1)

    assert [event.id for event in events] == ["evt_1", "evt_2"]


def test_get_event_by_id_returns_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/events/evt_01hw"
        return httpx.Response(200, json=event_payload())

    event = client_with_handler(handler).get_event_by_id("evt_01hw")

    assert event is not None
    assert event.id == "evt_01hw"
    assert event.capacity == 200


def test_get_event_by_id_returns_none_on_404() -> None:
    client = client_with_handler(lambda _: httpx.Response(404))

    assert client.get_event_by_id("missing") is None


def test_get_events_metrics_returns_metrics() -> None:
    client = client_with_handler(
        lambda request: httpx.Response(
            200,
            json={
                "total_events": 1,
                "total_activitys": 1,
                "total_capacity": 200,
                "total_enrolled": 87,
                "total_available_spots": 113,
                "average_occupancy_percentage": 43.5,
                "events_by_category": {"tecnologia": 1},
                "events_by_status": {"upcoming": 1, "ongoing": 0, "past": 0},
            },
        )
    )

    metrics = client.get_events_metrics()

    assert metrics.total_events == 1
    assert metrics.events_by_status.upcoming == 1


def test_list_event_activities_uses_activitys_route() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/events/evt_01hw/activitys"
        return httpx.Response(
            200,
            json=[
                {
                    "id_activity": "sec_01hw",
                    "title_activity": "Introdução à IA",
                    "description_activity": "Seção introdutória.",
                    "type": "palestra",
                    "starts_at": "2026-06-15T19:00:00-03:00",
                    "ends_at": "2026-06-15T20:00:00-03:00",
                    "timezone": "America/Sao_Paulo",
                    "thumbnail_url": "https://example.com/thumb.jpg",
                    "capacity_activity": 200,
                    "workload_minutes": 60,
                    "category_activity": "tecnologia",
                    "language_activity": "pt-BR",
                    "created_at": "2026-05-01T10:00:00Z",
                    "updated_at": "2026-05-10T08:30:00Z",
                    "deleted_at": None,
                    "deleted_by": None,
                    "created_by": "usr_123",
                }
            ],
        )

    activities = client_with_handler(handler).list_event_activities("evt_01hw")

    assert activities[0].id_activity == "sec_01hw"
    assert activities[0].workload_minutes == 60


def test_list_event_roles_uses_roles_route() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/events/evt_01hw/roles"
        return httpx.Response(200, json=[{"event_id": "evt_01hw", "role": "staff"}])

    roles = client_with_handler(handler).list_event_roles("evt_01hw")

    assert roles[0].event_id == "evt_01hw"
    assert roles[0].role == "staff"


@pytest.mark.parametrize("status_code", [400, 422])
def test_events_client_propagates_events_service_4xx(status_code: int) -> None:
    client = client_with_handler(lambda _: httpx.Response(status_code))

    with pytest.raises(HTTPException) as exc_info:
        client.list_events()

    assert exc_info.value.status_code == status_code


@pytest.mark.parametrize(
    "transport",
    [
        httpx.MockTransport(lambda _: httpx.Response(500)),
        httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                httpx.TimeoutException("timeout", request=request)
            )
        ),
    ],
)
def test_events_client_maps_events_service_unavailable(
    transport: httpx.MockTransport,
) -> None:
    client = EventsClient(
        base_url="http://events.test",
        transport=transport,
        token_provider=lambda **_: "test-token",
    )

    with pytest.raises(HTTPException) as exc_info:
        client.list_events()

    assert exc_info.value.status_code == 503


def test_request_sends_bearer_service_token() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"data": [event_payload()], "total": 1, "page": 1, "limit": 20},
        )

    client_with_handler(handler).list_events()

    assert seen["auth"] == "Bearer test-token"


def test_request_refreshes_token_and_retries_on_401() -> None:
    calls: list[str | None] = []

    def provider(*, force_refresh: bool = False) -> str:
        return "new-token" if force_refresh else "old-token"

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        calls.append(auth)
        if auth == "Bearer old-token":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(
            200,
            json={"data": [event_payload()], "total": 1, "page": 1, "limit": 20},
        )

    client = EventsClient(
        base_url="http://events.test",
        transport=httpx.MockTransport(handler),
        token_provider=provider,
    )

    result = client.list_events()

    assert result.total == 1
    assert calls == ["Bearer old-token", "Bearer new-token"]
