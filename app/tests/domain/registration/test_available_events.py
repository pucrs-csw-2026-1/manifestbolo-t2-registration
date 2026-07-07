"""Tests for available event listing without external services."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from main import app
from src.domain.events.client import get_events_client
from src.domain.events.schemas import EventResponse
from src.domain.registration.schemas import AvailableEventResponse
from src.domain.registration.service import (
    RegistrationService,
    get_registration_service,
)


class FakeEventsClient:
    def __init__(self, events: list[EventResponse]) -> None:
        self.events = events

    def get_all_events(self) -> list[EventResponse]:
        return self.events


class FakeRegistrationRepository:
    def __init__(self, registration_counts: dict[UUID, int]) -> None:
        self.registration_counts = registration_counts
        self.counted_event_ids: list[UUID] = []

    def count_registered_users_by_event_ids(
        self,
        event_ids: list[UUID],
    ) -> dict[UUID, int]:
        self.counted_event_ids = event_ids
        return {
            event_id: self.registration_counts.get(event_id, 0)
            for event_id in event_ids
        }


class FakeRegistrationService:
    def list_available_events(
        self,
        events_client: object,
    ) -> list[AvailableEventResponse]:
        _ = events_client
        return [
            AvailableEventResponse(
                eventId="evt_01",
                name="Evento disponível",
                maxCapacity=10,
                registeredCount=3,
                availableSlots=7,
            )
        ]


def event_response(
    event_id: str,
    *,
    title: str = "Evento Teste",
    capacity: int = 10,
    ends_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> EventResponse:
    now = datetime.now(UTC)
    return EventResponse(
        id=event_id,
        title=title,
        starts_at=now + timedelta(days=1),
        ends_at=ends_at or now + timedelta(days=2),
        timezone="America/Sao_Paulo",
        capacity=capacity,
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
        deleted_by=None,
        created_by="usr_123",
    )


def test_service_lists_only_open_not_full_events() -> None:
    available_event_id = uuid4()
    full_event_id = uuid4()
    removed_event_id = uuid4()
    past_event_id = uuid4()
    now = datetime.now(UTC)

    repository = FakeRegistrationRepository(
        {
            available_event_id: 2,
            full_event_id: 1,
        }
    )
    service = RegistrationService(repository)  # type: ignore[arg-type]
    events_client = FakeEventsClient(
        [
            event_response(
                str(available_event_id),
                title="Evento com vagas",
                capacity=3,
            ),
            event_response(str(full_event_id), title="Evento lotado", capacity=1),
            event_response(
                str(removed_event_id),
                title="Evento removido",
                deleted_at=now,
            ),
            event_response(
                str(past_event_id),
                title="Evento encerrado",
                ends_at=now - timedelta(minutes=1),
            ),
        ]
    )

    available_events = service.list_available_events(events_client)  # type: ignore[arg-type]

    assert repository.counted_event_ids == [available_event_id, full_event_id]
    assert len(available_events) == 1
    result = available_events[0]
    assert result.event_id == str(available_event_id)
    assert result.name == "Evento com vagas"
    assert result.max_capacity == 3
    assert result.registered_count == 2
    assert result.available_slots == 1


def test_available_events_endpoint_uses_registration_service() -> None:
    app.dependency_overrides[get_registration_service] = FakeRegistrationService
    app.dependency_overrides[get_events_client] = lambda: object()

    try:
        with TestClient(app) as client:
            response = client.get("/events/available")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "eventId": "evt_01",
            "name": "Evento disponível",
            "maxCapacity": 10,
            "registeredCount": 3,
            "availableSlots": 7,
            "description": None,
            "category": None,
            "startsAt": None,
            "endsAt": None,
            "registrationDeadline": None,
            "venue": None,
            "city": None,
        }
    ]
