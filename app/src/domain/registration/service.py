"""Business logic for registrations."""

from datetime import UTC, datetime, timedelta
import secrets
from string import ascii_letters, digits
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from src.config import get_settings
from src.domain.events.client import EventsClient
from src.domain.events.schemas import ActivityResponse, EventResponse

from .enums import RegistrationStatus
from .model import ActivityRegistration, Registration, ValidationToken
from .repository import RegistrationRepository, get_registration_repository
from .schemas import AvailableEventResponse


class RegistrationService:
    def __init__(self, repository: RegistrationRepository) -> None:
        self.repository = repository

    def _generate_authentication_token(self) -> str:
        alphabet = ascii_letters + digits
        return "".join(secrets.choice(alphabet) for _ in range(8))

    def _authentication_token_expires_at(self) -> datetime:
        settings = get_settings()
        return datetime.now(UTC) + timedelta(
            minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        )

    def register(
        self,
        event_id: UUID,
        user_id: UUID,
        authenticated_user_id: UUID,
        events_client: EventsClient,
        allow_different_user: bool = False,
    ) -> tuple[Registration, ValidationToken]:
        if authenticated_user_id != user_id and not allow_different_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authenticated user does not match requested user",
            )

        self.repository.acquire_event_registration_lock(event_id)
        self._validate_event_registration_is_open(event_id, events_client)

        if self.repository.get_by_event_and_user(event_id, user_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already registered for this event",
            )

        try:
            return self.repository.create_with_authentication_token(
                event_id,
                user_id,
                self._generate_authentication_token(),
                self._authentication_token_expires_at(),
            )
        except IntegrityError as exc:
            self.repository.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already registered for this event",
            ) from exc

    def list_event_registrations(self, event_id: UUID) -> list[Registration]:
        return self.repository.list_by_event(event_id)

    def list_user_registrations(self, user_id: UUID) -> list[Registration]:
        return self.repository.list_by_user(user_id)

    def list_user_activity_registrations(
        self, user_id: UUID
    ) -> list[ActivityRegistration]:
        return self.repository.list_activity_registrations_by_user(user_id)

    def list_available_events(
        self,
        events_client: EventsClient,
    ) -> list[AvailableEventResponse]:
        now = datetime.now(UTC)
        events = events_client.get_all_events()
        open_events = [
            event for event in events if self._accepts_registration(event, now)
        ]

        event_uuids_by_id = self._event_uuids_by_id(open_events)
        registration_counts = self.repository.count_registered_users_by_event_ids(
            list(event_uuids_by_id.values())
        )

        available_events: list[AvailableEventResponse] = []
        for event in open_events:
            event_uuid = event_uuids_by_id.get(event.id)
            registered_count = (
                registration_counts.get(event_uuid, 0) if event_uuid else 0
            )
            available_slots = event.capacity - registered_count

            if available_slots <= 0:
                continue

            available_events.append(
                AvailableEventResponse(
                    eventId=event.id,
                    name=event.title,
                    maxCapacity=event.capacity,
                    registeredCount=registered_count,
                    availableSlots=available_slots,
                )
            )

        return available_events

    def list_activity_user_ids(self, activity_id: UUID) -> list[UUID]:
        return self.repository.list_user_ids_by_activity(activity_id)

    def get_check_in_registration(
        self,
        event_id: UUID,
        user_id: UUID,
    ) -> Registration | None:
        return self.repository.get_by_event_and_user(event_id, user_id)

    def confirm(self, confirmation_id: UUID, token: str) -> Registration:
        validation_token = self.repository.get_validation_token(confirmation_id)
        if validation_token is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Confirmation not found.",
            )

        if validation_token.expires_at < datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Confirmation token has expired.",
            )

        registration = self.repository.get_by_event_and_user(
            validation_token.event_id, validation_token.user_id
        )
        if registration is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Registration not found.",
            )

        if registration.status == RegistrationStatus.CONFIRMED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Registration already confirmed.",
            )

        if token != validation_token.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid confirmation token.",
            )

        return self.repository.update_status(registration, RegistrationStatus.CONFIRMED)

    def cancel_registration(self, event_id: UUID, user_id: UUID) -> None:
        registration = self.repository.get_by_event_and_user(event_id, user_id)
        if registration is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Registration not found.",
            )

        # TODO: rejeitar cancelamento com 422 quando o evento já tiver ocorrido.
        # Depende do contrato real do events-service (EventsClient hoje é apenas
        # um placeholder), então a checagem fica pendente até a integração existir.

        # Soft delete: mantém o histórico marcando a inscrição como CANCELLED.
        # Idempotente: cancelar uma inscrição já cancelada também retorna 204.
        if registration.status != RegistrationStatus.CANCELLED:
            self.repository.update_status(registration, RegistrationStatus.CANCELLED)

    def get_activity_registration(
        self,
        activity_id: UUID,
        user_id: UUID,
    ) -> ActivityRegistration | None:
        return self.repository.get_by_activity_and_user(activity_id, user_id)

    def register_activity(
        self,
        activity_id: UUID,
        user_id: UUID,
        event_id: UUID,
        events_client: EventsClient,
    ) -> ActivityRegistration:
        self._validate_event_registration_is_open(event_id, events_client)
        self._validate_activity_registration_is_open(
            activity_id,
            event_id,
            events_client,
        )

        if self.repository.get_by_activity_and_user(activity_id, user_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already registered for this activity",
            )

        try:
            return self.repository.create_activity_registration(
                activity_id,
                user_id,
                event_id,
            )
        except IntegrityError as exc:
            self.repository.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already registered for this activity",
            ) from exc

    @staticmethod
    def _accepts_registration(event: EventResponse, now: datetime) -> bool:
        if event.deleted_at is not None:
            return False

        ends_at = event.ends_at
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=UTC)

        return ends_at >= now

    def _validate_event_registration_is_open(
        self,
        event_id: UUID,
        events_client: EventsClient,
    ) -> EventResponse:
        event = events_client.get_event_by_id(event_id)
        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event not found.",
            )

        if not self._accepts_registration(event, datetime.now(UTC)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Event already happened or is no longer available.",
            )

        registered_count = self.repository.count_registered_users_by_event_id(event_id)
        if registered_count >= event.capacity:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Event is full.",
            )

        return event

    def _validate_activity_registration_is_open(
        self,
        activity_id: UUID,
        event_id: UUID,
        events_client: EventsClient,
    ) -> ActivityResponse:
        activity = self._get_activity(activity_id, event_id, events_client)
        if activity.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Activity is no longer available.",
            )

        if self._date_has_passed(activity.ends_at, datetime.now(UTC)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Activity already happened.",
            )

        if activity.capacity_activity is not None:
            registered_count = self.repository.count_by_activity_id(activity_id)
            if registered_count >= activity.capacity_activity:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Activity is full.",
                )

        return activity

    @staticmethod
    def _get_activity(
        activity_id: UUID,
        event_id: UUID,
        events_client: EventsClient,
    ) -> ActivityResponse:
        activities = events_client.list_event_activities(event_id)
        activity_id_text = str(activity_id)
        for activity in activities:
            if activity.id_activity == activity_id_text:
                return activity

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found.",
        )

    @staticmethod
    def _date_has_passed(date: datetime, now: datetime) -> bool:
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return date < now

    @staticmethod
    def _event_uuids_by_id(events: list[EventResponse]) -> dict[str, UUID]:
        event_uuids: dict[str, UUID] = {}
        for event in events:
            try:
                event_uuids[event.id] = UUID(event.id)
            except ValueError:
                continue
        return event_uuids


def get_registration_service(
    repository: RegistrationRepository = Depends(get_registration_repository),
) -> RegistrationService:
    return RegistrationService(repository)
