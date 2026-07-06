"""Tests for event registration."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from main import app
from src.database import Base
from src.domain.auth.client import get_auth_client
from src.domain.auth.schemas import UserResponse
from src.domain.events.client import get_events_client
from src.domain.events.schemas import ActivityResponse, EventResponse
from src.domain.registration.enums import RegistrationStatus
from src.domain.registration.model import (
    ActivityRegistration,
    Registration,
    ValidationToken,
)
from src.domain.registration.repository import RegistrationRepository
from src.domain.registration.service import RegistrationService


def _seed_registration_with_token(
    db_session: Session,
    *,
    token: str = "XY34ZW78",
    status: RegistrationStatus = RegistrationStatus.REGISTERED,
    expires_at: datetime | None = None,
) -> ValidationToken:
    """Create a registration plus its validation token and return the token."""
    event_id = uuid4()
    user_id = uuid4()
    db_session.add(Registration(event_id=event_id, user_id=user_id, status=status))
    validation_token = ValidationToken(
        event_id=event_id,
        user_id=user_id,
        token=token,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(validation_token)
    db_session.commit()
    db_session.refresh(validation_token)
    return validation_token


@pytest.mark.xfail(reason="POST /events/{event_id}/guests ainda não implementado (501)")
class FakeAuthClient:
    def __init__(
        self,
        user: UserResponse | None = None,
        exception: HTTPException | None = None,
    ) -> None:
        self.user = user
        self.exception = exception

    def validate_token(self, token: str) -> UserResponse:
        if self.exception is not None:
            raise self.exception
        if self.user is None:
            raise AssertionError("fake auth user not configured")
        return self.user


class FakeEventsClient:
    def __init__(
        self,
        events: list[EventResponse],
        activities: list[ActivityResponse] | None = None,
    ) -> None:
        self.events = events
        self.activities = activities or []

    def get_all_events(self) -> list[EventResponse]:
        return self.events

    def get_event_by_id(self, event_id: str | UUID) -> EventResponse | None:
        event_id_text = str(event_id)
        return next((event for event in self.events if event.id == event_id_text), None)

    def list_event_activities(self, event_id: str | UUID) -> list[ActivityResponse]:
        _ = event_id
        return self.activities


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


def activity_response(
    activity_id: str,
    *,
    title: str = "Atividade Teste",
    capacity: int | None = 10,
    ends_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> ActivityResponse:
    now = datetime.now(UTC)
    return ActivityResponse(
        id_activity=activity_id,
        title_activity=title,
        type="palestra",
        starts_at=now + timedelta(days=1),
        ends_at=ends_at or now + timedelta(days=2),
        timezone="America/Sao_Paulo",
        capacity_activity=capacity,
        workload_minutes=60,
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
        deleted_by=None,
        created_by="usr_123",
    )


def override_auth_user(user_id: UUID, access_level: str = "PARTICIPANT") -> None:
    user = UserResponse(
        id=user_id,
        email="user@example.com",
        username="user",
        access_level=access_level,
        is_active=True,
    )
    app.dependency_overrides[get_auth_client] = lambda: FakeAuthClient(user=user)


def override_auth_exception(exception: HTTPException) -> None:
    app.dependency_overrides[get_auth_client] = lambda: FakeAuthClient(
        exception=exception
    )


def override_events(
    events: list[EventResponse],
    activities: list[ActivityResponse] | None = None,
) -> None:
    app.dependency_overrides[get_events_client] = lambda: FakeEventsClient(
        events,
        activities,
    )


def override_open_event(event_id: UUID, capacity: int = 10) -> None:
    override_events([event_response(str(event_id), capacity=capacity)])


def test_register_endpoint_creates_registration(client: TestClient) -> None:
    event_id = uuid4()
    user_id = uuid4()
    override_auth_user(user_id)
    override_open_event(event_id)

    response = client.post(
        f"/events/{event_id}/guests",
        json={"userId": str(user_id)},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["eventId"] == str(event_id)
    assert payload["userId"] == str(user_id)
    assert payload["status"] == RegistrationStatus.REGISTERED.value
    assert payload["createdAt"] is not None
    assert payload["updatedAt"] is None


def test_list_available_events_crosses_events_capacity_with_local_registrations(
    client: TestClient,
    db_session: Session,
) -> None:
    available_event_id = uuid4()
    full_event_id = uuid4()
    removed_event_id = uuid4()
    past_event_id = uuid4()
    now = datetime.now(UTC)
    user_registered_in_event_and_activity = uuid4()

    db_session.add(
        Registration(
            event_id=available_event_id,
            user_id=user_registered_in_event_and_activity,
        ),
    )
    db_session.add(
        ActivityRegistration(
            event_id=available_event_id,
            user_id=user_registered_in_event_and_activity,
            activity_id=uuid4(),
        )
    )
    db_session.add(
        Registration(
            event_id=available_event_id,
            user_id=uuid4(),
            status=RegistrationStatus.CONFIRMED,
        ),
    )
    db_session.add(
        Registration(
            event_id=available_event_id,
            user_id=uuid4(),
            status=RegistrationStatus.CANCELLED,
        ),
    )
    db_session.add(Registration(event_id=full_event_id, user_id=uuid4()))
    db_session.add(
        ActivityRegistration(
            event_id=full_event_id,
            user_id=uuid4(),
            activity_id=uuid4(),
        )
    )
    db_session.commit()

    override_events(
        [
            event_response(
                str(available_event_id),
                title="Evento com vagas",
                capacity=3,
            ),
            event_response(str(full_event_id), title="Evento lotado", capacity=2),
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

    response = client.get("/events/available")

    assert response.status_code == 200
    assert response.json() == [
        {
            "eventId": str(available_event_id),
            "name": "Evento com vagas",
            "maxCapacity": 3,
            "registeredCount": 2,
            "availableSlots": 1,
        }
    ]


def test_register_endpoint_creates_authentication_token(
    client: TestClient, db_session: Session
) -> None:
    event_id = uuid4()
    user_id = uuid4()
    override_auth_user(user_id)
    override_open_event(event_id)

    response = client.post(
        f"/events/{event_id}/guests",
        json={"userId": str(user_id)},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 201

    auth_token = (
        db_session.query(ValidationToken)
        .filter_by(
            event_id=event_id,
            user_id=user_id,
        )
        .one()
    )

    assert auth_token.token is not None
    assert len(auth_token.token) == 8
    assert auth_token.token.isalnum()
    assert auth_token.expires_at is not None
    assert auth_token.expires_at > auth_token.created_at


def test_registration_repository_persists_row(db_session: Session) -> None:
    repository = RegistrationRepository(db_session)
    event_id = uuid4()
    user_id = uuid4()

    registration = repository.create(event_id, user_id)

    assert registration.event_id == event_id
    assert registration.user_id == user_id
    assert registration.status == RegistrationStatus.REGISTERED
    assert registration.created_at is not None
    assert registration.updated_at is None


def test_registration_service_rejects_duplicates(db_session: Session) -> None:
    repository = RegistrationRepository(db_session)
    service = RegistrationService(repository)
    event_id = uuid4()
    user_id = uuid4()
    events_client = FakeEventsClient([event_response(str(event_id))])

    service.register(event_id, user_id, user_id, events_client)  # type: ignore[arg-type]

    try:
        service.register(event_id, user_id, user_id, events_client)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("expected duplicate registration to fail")


def test_registration_service_rejects_identity_mismatch(
    db_session: Session,
) -> None:
    repository = RegistrationRepository(db_session)
    service = RegistrationService(repository)

    with pytest.raises(HTTPException) as exc_info:
        service.register(
            uuid4(),
            uuid4(),
            uuid4(),
            FakeEventsClient([]),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403


def test_post_register_requires_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/register",
        json={"eventId": str(uuid4()), "userId": str(uuid4())},
    )

    assert response.status_code == 401


def test_post_register_accepts_matching_authenticated_user(
    client: TestClient,
) -> None:
    event_id = uuid4()
    user_id = uuid4()
    override_auth_user(user_id)
    override_open_event(event_id)

    response = client.post(
        "/register",
        json={"eventId": str(event_id), "userId": str(user_id)},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["eventId"] == str(event_id)
    assert payload["userId"] == str(user_id)


def test_post_register_rejects_different_authenticated_user(
    client: TestClient,
) -> None:
    override_auth_user(uuid4())

    response = client.post(
        "/register",
        json={"eventId": str(uuid4()), "userId": str(uuid4())},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 403


def test_post_register_allows_admin_to_register_other_user(
    client: TestClient,
) -> None:
    event_id = uuid4()
    target_user_id = uuid4()
    override_auth_user(uuid4(), "ADMIN")
    override_open_event(event_id)

    response = client.post(
        "/register",
        json={"eventId": str(event_id), "userId": str(target_user_id)},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["eventId"] == str(event_id)
    assert payload["userId"] == str(target_user_id)


def test_post_register_rejects_past_event(client: TestClient) -> None:
    event_id = uuid4()
    user_id = uuid4()
    now = datetime.now(UTC)
    override_auth_user(user_id)
    override_events([event_response(str(event_id), ends_at=now - timedelta(minutes=1))])

    response = client.post(
        "/register",
        json={"eventId": str(event_id), "userId": str(user_id)},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"] == "Event already happened or is no longer available."
    )


def test_post_register_rejects_full_event(
    client: TestClient,
    db_session: Session,
) -> None:
    event_id = uuid4()
    user_id = uuid4()
    db_session.add(Registration(event_id=event_id, user_id=uuid4()))
    db_session.commit()
    override_auth_user(user_id)
    override_open_event(event_id, capacity=1)

    response = client.post(
        "/register",
        json={"eventId": str(event_id), "userId": str(user_id)},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Event is full."


def test_register_guest_rejects_full_event(
    client: TestClient,
    db_session: Session,
) -> None:
    event_id = uuid4()
    user_id = uuid4()
    db_session.add(Registration(event_id=event_id, user_id=uuid4()))
    db_session.commit()
    override_auth_user(user_id)
    override_open_event(event_id, capacity=1)

    response = client.post(
        f"/events/{event_id}/guests",
        json={"userId": str(user_id)},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Event is full."


def test_register_activity_rejects_full_event_from_activity_registrations(
    client: TestClient,
    db_session: Session,
) -> None:
    event_id = uuid4()
    activity_id = uuid4()
    user_id = uuid4()
    db_session.add(
        ActivityRegistration(
            activity_id=uuid4(),
            user_id=uuid4(),
            event_id=event_id,
        )
    )
    db_session.commit()
    override_auth_user(user_id)
    override_events(
        [event_response(str(event_id), capacity=1)],
        [activity_response(str(activity_id))],
    )

    response = client.post(
        "/activities/registrations",
        json={
            "activityId": str(activity_id),
            "userId": str(user_id),
            "eventId": str(event_id),
        },
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Event is full."


def test_post_register_returns_503_when_auth_fails(
    client: TestClient,
) -> None:
    override_auth_exception(
        HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable",
        )
    )

    response = client.post(
        "/register",
        json={"eventId": str(uuid4()), "userId": str(uuid4())},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 503


def test_registration_model_defaults_timestamp(db_session: Session) -> None:
    registration = Registration(event_id=uuid4(), user_id=uuid4())
    db_session.add(registration)
    db_session.commit()

    assert registration.status == RegistrationStatus.REGISTERED
    assert registration.created_at is not None
    assert isinstance(registration.created_at, datetime)
    assert registration.created_at.tzinfo is not None
    assert registration.updated_at is None


def test_registration_updated_at_is_populated_on_update(db_session: Session) -> None:
    registration = Registration(event_id=uuid4(), user_id=uuid4())
    db_session.add(registration)
    db_session.commit()

    registration.status = RegistrationStatus.CONFIRMED
    db_session.commit()
    db_session.refresh(registration)

    assert registration.status == RegistrationStatus.CONFIRMED
    assert registration.updated_at is not None


def test_list_event_registrations_requires_manager_or_admin(
    client: TestClient,
) -> None:
    override_auth_user(uuid4())

    response = client.get(
        f"/events/{uuid4()}/registrations",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 403


def test_list_event_registrations_returns_rows_for_manager(
    client: TestClient,
    db_session: Session,
) -> None:
    event_id = uuid4()
    user_ids = [uuid4(), uuid4()]
    for user_id in user_ids:
        db_session.add(Registration(event_id=event_id, user_id=user_id))
    db_session.commit()
    override_auth_user(uuid4(), "MANAGER")

    response = client.get(
        f"/events/{event_id}/registrations",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [row["userId"] for row in payload] == [str(uid) for uid in user_ids]


def test_validate_check_in_returns_registration_state(
    client: TestClient, db_session: Session
) -> None:
    event_id = uuid4()
    user_id = uuid4()
    registration = Registration(
        event_id=event_id,
        user_id=user_id,
        status=RegistrationStatus.CONFIRMED,
    )
    db_session.add(registration)
    db_session.commit()
    override_auth_user(uuid4(), "MANAGER")

    response = client.get(
        f"/events/{event_id}/guests/{user_id}/check-in",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["eventId"] == str(event_id)
    assert payload["userId"] == str(user_id)
    assert payload["status"] == RegistrationStatus.CONFIRMED.value
    assert payload["createdAt"] is not None
    assert payload["updatedAt"] is None


def test_validate_check_in_returns_false_for_missing_registration(
    client: TestClient,
) -> None:
    event_id = uuid4()
    user_id = uuid4()
    override_auth_user(uuid4(), "MANAGER")

    response = client.get(
        f"/events/{event_id}/guests/{user_id}/check-in",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 404


def test_confirm_registration_succeeds_with_valid_token(
    client: TestClient, db_session: Session
) -> None:
    validation_token = _seed_registration_with_token(db_session, token="ABCD1234")

    response = client.post(
        f"/events/confirmation/{validation_token.id}",
        json={"token": "ABCD1234"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["confirmationId"] == str(validation_token.id)
    assert payload["eventId"] == str(validation_token.event_id)
    assert payload["userId"] == str(validation_token.user_id)
    assert payload["confirmedAt"] is not None

    registration = RegistrationRepository(db_session).get_by_event_and_user(
        validation_token.event_id, validation_token.user_id
    )
    assert registration is not None
    assert registration.status == RegistrationStatus.CONFIRMED


def test_confirm_registration_rejects_wrong_token(
    client: TestClient, db_session: Session
) -> None:
    validation_token = _seed_registration_with_token(db_session, token="ABCD1234")

    response = client.post(
        f"/events/confirmation/{validation_token.id}",
        json={"token": "WRONG999"},
    )

    assert response.status_code == 400


def test_confirm_registration_rejects_expired_token(
    client: TestClient, db_session: Session
) -> None:
    validation_token = _seed_registration_with_token(
        db_session,
        token="ABCD1234",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )

    response = client.post(
        f"/events/confirmation/{validation_token.id}",
        json={"token": "ABCD1234"},
    )

    assert response.status_code == 410


def test_confirm_registration_rejects_already_confirmed(
    client: TestClient, db_session: Session
) -> None:
    validation_token = _seed_registration_with_token(
        db_session,
        token="ABCD1234",
        status=RegistrationStatus.CONFIRMED,
    )

    response = client.post(
        f"/events/confirmation/{validation_token.id}",
        json={"token": "ABCD1234"},
    )

    assert response.status_code == 409


def test_confirm_registration_returns_404_for_unknown_confirmation(
    client: TestClient,
) -> None:
    response = client.post(
        f"/events/confirmation/{uuid4()}",
        json={"token": "ABCD1234"},
    )

    assert response.status_code == 404


def test_validate_check_in_rejects_participant_role(client: TestClient) -> None:
    override_auth_user(uuid4(), "PARTICIPANT")

    response = client.get(
        f"/events/{uuid4()}/guests/{uuid4()}/check-in",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 403


def test_cancel_registration_soft_deletes_existing(
    client: TestClient, db_session: Session
) -> None:
    event_id = uuid4()
    user_id = uuid4()
    db_session.add(Registration(event_id=event_id, user_id=user_id))
    db_session.commit()
    override_auth_user(user_id)

    response = client.delete(
        f"/events/{event_id}/guests/{user_id}",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 204

    registration = RegistrationRepository(db_session).get_by_event_and_user(
        event_id, user_id
    )
    assert registration is not None
    assert registration.status == RegistrationStatus.CANCELLED


def test_confirm_registration_rejects_malformed_token(client: TestClient) -> None:
    response = client.post(
        f"/events/confirmation/{uuid4()}",
        json={"token": "short"},
    )

    assert response.status_code == 422


def test_cancel_registration_rejects_other_participant(client: TestClient) -> None:
    override_auth_user(uuid4())

    response = client.delete(
        f"/events/{uuid4()}/guests/{uuid4()}",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 403


def test_list_activity_registrations_returns_user_ids(
    client: TestClient, db_session: Session
) -> None:
    activity_id = uuid4()
    event_id = uuid4()
    user_ids = [uuid4(), uuid4()]
    for user_id in user_ids:
        db_session.add(
            ActivityRegistration(
                activity_id=activity_id,
                user_id=user_id,
                event_id=event_id,
            )
        )
    db_session.commit()
    override_auth_user(uuid4(), "MANAGER")

    response = client.get(
        f"/activities/{activity_id}/registrations",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    assert sorted(response.json()) == sorted(str(uid) for uid in user_ids)


def test_list_activity_registrations_returns_empty_list_when_none(
    client: TestClient,
) -> None:
    activity_id = uuid4()
    override_auth_user(uuid4(), "MANAGER")

    response = client.get(
        f"/activities/{activity_id}/registrations",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_list_activity_registrations_rejects_participant(client: TestClient) -> None:
    override_auth_user(uuid4())

    response = client.get(
        f"/activities/{uuid4()}/registrations",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 403


def test_activity_registration_repository_finds_row(db_session: Session) -> None:
    repository = RegistrationRepository(db_session)
    activity_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()

    db_session.add(
        ActivityRegistration(
            activity_id=activity_id,
            user_id=user_id,
            event_id=event_id,
        )
    )
    db_session.commit()

    registration = repository.get_by_activity_and_user(activity_id, user_id)

    assert registration is not None
    assert registration.activity_id == activity_id
    assert registration.user_id == user_id
    assert registration.event_id == event_id


def test_activity_registration_service_returns_row(db_session: Session) -> None:
    repository = RegistrationRepository(db_session)
    service = RegistrationService(repository)
    activity_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()

    db_session.add(
        ActivityRegistration(
            activity_id=activity_id,
            user_id=user_id,
            event_id=event_id,
        )
    )
    db_session.commit()

    registration = service.get_activity_registration(activity_id, user_id)

    assert registration is not None
    assert registration.activity_id == activity_id
    assert registration.user_id == user_id
    assert registration.event_id == event_id


def test_get_activity_registration_endpoint_returns_row(
    client: TestClient, db_session: Session
) -> None:
    activity_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()

    db_session.add(
        ActivityRegistration(
            activity_id=activity_id,
            user_id=user_id,
            event_id=event_id,
        )
    )
    db_session.commit()
    override_auth_user(user_id)

    response = client.get(
        f"/activities/{activity_id}/users/{user_id}",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["activityId"] == str(activity_id)
    assert payload["userId"] == str(user_id)
    assert payload["eventId"] == str(event_id)
    assert payload["createdAt"] is not None
    assert payload["updatedAt"] is not None


def test_activity_registration_repository_creates_row(db_session: Session) -> None:
    repository = RegistrationRepository(db_session)
    activity_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()

    registration = repository.create_activity_registration(
        activity_id,
        user_id,
        event_id,
    )

    assert registration.activity_id == activity_id
    assert registration.user_id == user_id
    assert registration.event_id == event_id
    assert registration.created_at is not None
    assert registration.updated_at is not None


def test_activity_registration_service_creates_row(db_session: Session) -> None:
    repository = RegistrationRepository(db_session)
    service = RegistrationService(repository)
    activity_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()
    events_client = FakeEventsClient(
        [event_response(str(event_id))],
        [activity_response(str(activity_id))],
    )

    registration = service.register_activity(
        activity_id,
        user_id,
        event_id,
        events_client,  # type: ignore[arg-type]
    )

    assert registration.activity_id == activity_id
    assert registration.user_id == user_id
    assert registration.event_id == event_id


def test_post_activity_registration_endpoint_creates_row(
    client: TestClient, db_session: Session
) -> None:
    activity_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()
    override_auth_user(user_id)
    override_events(
        [event_response(str(event_id))],
        [activity_response(str(activity_id))],
    )

    response = client.post(
        "/activities/registrations",
        json={
            "activityId": str(activity_id),
            "userId": str(user_id),
            "eventId": str(event_id),
        },
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["activityId"] == str(activity_id)
    assert payload["userId"] == str(user_id)
    assert payload["eventId"] == str(event_id)
    assert payload["createdAt"] is not None
    assert payload["updatedAt"] is not None


def test_post_activity_registration_rejects_other_participant(
    client: TestClient,
) -> None:
    override_auth_user(uuid4())

    response = client.post(
        "/activities/registrations",
        json={
            "activityId": str(uuid4()),
            "userId": str(uuid4()),
            "eventId": str(uuid4()),
        },
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 403


def test_post_activity_registration_rejects_past_activity(client: TestClient) -> None:
    activity_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()
    now = datetime.now(UTC)
    override_auth_user(user_id)
    override_events(
        [event_response(str(event_id))],
        [activity_response(str(activity_id), ends_at=now - timedelta(minutes=1))],
    )

    response = client.post(
        "/activities/registrations",
        json={
            "activityId": str(activity_id),
            "userId": str(user_id),
            "eventId": str(event_id),
        },
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Activity already happened."


def test_post_activity_registration_rejects_full_activity(
    client: TestClient,
    db_session: Session,
) -> None:
    activity_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()
    db_session.add(
        ActivityRegistration(
            activity_id=activity_id,
            user_id=uuid4(),
            event_id=event_id,
        )
    )
    db_session.commit()
    override_auth_user(user_id)
    override_events(
        [event_response(str(event_id))],
        [activity_response(str(activity_id), capacity=1)],
    )

    response = client.post(
        "/activities/registrations",
        json={
            "activityId": str(activity_id),
            "userId": str(user_id),
            "eventId": str(event_id),
        },
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Activity is full."


def test_validation_token_belongs_to_registration_domain_metadata() -> None:
    assert ValidationToken.__table__.name == "authentication_tokens"
    assert Base.metadata.tables["authentication_tokens"] is ValidationToken.__table__


def test_register_response_exposes_confirmation_id_and_token(
    client: TestClient, db_session: Session
) -> None:
    event_id = uuid4()
    user_id = uuid4()
    override_auth_user(user_id)
    override_open_event(event_id)

    response = client.post(
        f"/events/{event_id}/guests",
        json={"userId": str(user_id)},
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 201
    payload = response.json()

    auth_token = (
        db_session.query(ValidationToken)
        .filter_by(event_id=event_id, user_id=user_id)
        .one()
    )
    assert payload["confirmationId"] == str(auth_token.id)
    assert payload["confirmationToken"] == auth_token.token
    assert len(payload["confirmationToken"]) == 8


def test_list_user_registrations_returns_own_rows(
    client: TestClient, db_session: Session
) -> None:
    user_id = uuid4()
    other_user_id = uuid4()
    own_event_ids = [uuid4(), uuid4()]
    for event_id in own_event_ids:
        db_session.add(Registration(event_id=event_id, user_id=user_id))
    db_session.add(Registration(event_id=uuid4(), user_id=other_user_id))
    db_session.commit()
    override_auth_user(user_id)

    response = client.get(
        f"/users/{user_id}/registrations",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert {row["eventId"] for row in payload} == {str(e) for e in own_event_ids}
    assert all(row["userId"] == str(user_id) for row in payload)


def test_list_user_registrations_rejects_other_participant(
    client: TestClient,
) -> None:
    override_auth_user(uuid4())

    response = client.get(
        f"/users/{uuid4()}/registrations",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 403


def test_list_user_registrations_allows_manager_for_any_user(
    client: TestClient, db_session: Session
) -> None:
    target_user_id = uuid4()
    event_id = uuid4()
    db_session.add(Registration(event_id=event_id, user_id=target_user_id))
    db_session.commit()
    override_auth_user(uuid4(), "MANAGER")

    response = client.get(
        f"/users/{target_user_id}/registrations",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    assert [row["eventId"] for row in response.json()] == [str(event_id)]


def test_list_user_activity_registrations_returns_own_rows(
    client: TestClient, db_session: Session
) -> None:
    user_id = uuid4()
    event_id = uuid4()
    activity_ids = [uuid4(), uuid4()]
    for activity_id in activity_ids:
        db_session.add(
            ActivityRegistration(
                activity_id=activity_id,
                user_id=user_id,
                event_id=event_id,
            )
        )
    db_session.add(
        ActivityRegistration(
            activity_id=uuid4(),
            user_id=uuid4(),
            event_id=event_id,
        )
    )
    db_session.commit()
    override_auth_user(user_id)

    response = client.get(
        f"/users/{user_id}/activities",
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert {row["activityId"] for row in payload} == {str(a) for a in activity_ids}
    assert all(row["userId"] == str(user_id) for row in payload)
