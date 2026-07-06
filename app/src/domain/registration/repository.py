"""Repository for registration persistence."""

from collections import defaultdict
from collections.abc import Generator
from datetime import datetime
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.database import get_db

from .enums import RegistrationStatus
from .model import ActivityRegistration, Registration, ValidationToken


class RegistrationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def acquire_event_registration_lock(self, event_id: UUID) -> None:
        key1, key2 = self._uuid_advisory_lock_keys(event_id)
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:key1, :key2)"),
            {"key1": key1, "key2": key2},
        )

    def get_by_event_and_user(
        self, event_id: UUID, user_id: UUID
    ) -> Registration | None:
        return (
            self.db.query(Registration)
            .filter(
                Registration.event_id == event_id,
                Registration.user_id == user_id,
            )
            .first()
        )

    def get_by_activity_and_user(
        self, activity_id: UUID, user_id: UUID
    ) -> ActivityRegistration | None:
        return (
            self.db.query(ActivityRegistration)
            .filter(
                ActivityRegistration.activity_id == activity_id,
                ActivityRegistration.user_id == user_id,
            )
            .first()
        )

    def create_activity_registration(
        self, activity_id: UUID, user_id: UUID, event_id: UUID
    ) -> ActivityRegistration:
        registration = ActivityRegistration(
            activity_id=activity_id,
            user_id=user_id,
            event_id=event_id,
        )
        self.db.add(registration)
        self.db.commit()
        self.db.refresh(registration)
        return registration

    def list_by_event(self, event_id: UUID) -> list[Registration]:
        return (
            self.db.query(Registration)
            .filter(Registration.event_id == event_id)
            .order_by(Registration.created_at)
            .all()
        )

    def list_by_user(self, user_id: UUID) -> list[Registration]:
        return (
            self.db.query(Registration)
            .filter(Registration.user_id == user_id)
            .order_by(Registration.created_at)
            .all()
        )

    def list_activity_registrations_by_user(
        self, user_id: UUID
    ) -> list[ActivityRegistration]:
        return (
            self.db.query(ActivityRegistration)
            .filter(ActivityRegistration.user_id == user_id)
            .order_by(ActivityRegistration.created_at)
            .all()
        )

    def count_active_by_event_ids(self, event_ids: list[UUID]) -> dict[UUID, int]:
        if not event_ids:
            return {}

        rows = (
            self.db.query(Registration.event_id, func.count(Registration.user_id))
            .filter(
                Registration.event_id.in_(event_ids),
                Registration.status != RegistrationStatus.CANCELLED,
            )
            .group_by(Registration.event_id)
            .all()
        )
        return {event_id: count for event_id, count in rows}

    def count_active_by_event_id(self, event_id: UUID) -> int:
        return self.count_active_by_event_ids([event_id]).get(event_id, 0)

    def count_registered_users_by_event_ids(
        self, event_ids: list[UUID]
    ) -> dict[UUID, int]:
        if not event_ids:
            return {}

        users_by_event: dict[UUID, set[UUID]] = defaultdict(set)

        registration_rows = (
            self.db.query(Registration.event_id, Registration.user_id)
            .filter(
                Registration.event_id.in_(event_ids),
                Registration.status != RegistrationStatus.CANCELLED,
            )
            .all()
        )
        activity_registration_rows = (
            self.db.query(ActivityRegistration.event_id, ActivityRegistration.user_id)
            .filter(ActivityRegistration.event_id.in_(event_ids))
            .all()
        )

        for event_id, user_id in registration_rows + activity_registration_rows:
            users_by_event[event_id].add(user_id)

        return {
            event_id: len(user_ids) for event_id, user_ids in users_by_event.items()
        }

    def count_registered_users_by_event_id(self, event_id: UUID) -> int:
        return self.count_registered_users_by_event_ids([event_id]).get(event_id, 0)

    def count_by_activity_id(self, activity_id: UUID) -> int:
        return (
            self.db.query(func.count(ActivityRegistration.user_id))
            .filter(ActivityRegistration.activity_id == activity_id)
            .scalar()
            or 0
        )

    def list_user_ids_by_activity(self, activity_id: UUID) -> list[UUID]:
        rows = (
            self.db.query(ActivityRegistration.user_id)
            .filter(ActivityRegistration.activity_id == activity_id)
            .order_by(ActivityRegistration.created_at)
            .all()
        )
        return [row.user_id for row in rows]

    def create(self, event_id: UUID, user_id: UUID) -> Registration:
        registration = Registration(event_id=event_id, user_id=user_id)
        self.db.add(registration)
        self.db.commit()
        self.db.refresh(registration)
        return registration

    def get_validation_token(self, confirmation_id: UUID) -> ValidationToken | None:
        return (
            self.db.query(ValidationToken)
            .filter(ValidationToken.id == confirmation_id)
            .first()
        )

    def create_with_authentication_token(
        self,
        event_id: UUID,
        user_id: UUID,
        token: str,
        expires_at: datetime,
    ) -> tuple[Registration, ValidationToken]:
        registration = Registration(event_id=event_id, user_id=user_id)
        authentication_token = ValidationToken(
            event_id=event_id,
            user_id=user_id,
            token=token,
            expires_at=expires_at,
        )
        self.db.add(registration)
        self.db.add(authentication_token)
        self.db.commit()
        self.db.refresh(registration)
        self.db.refresh(authentication_token)
        return registration, authentication_token

    def create_authentication_token(
        self,
        event_id: UUID,
        user_id: UUID,
        token: str,
        expires_at: datetime,
    ) -> ValidationToken:
        authentication_token = ValidationToken(
            event_id=event_id,
            user_id=user_id,
            token=token,
            expires_at=expires_at,
        )
        self.db.add(authentication_token)
        self.db.commit()
        self.db.refresh(authentication_token)
        return authentication_token

    def update_status(
        self, registration: Registration, new_status: RegistrationStatus
    ) -> Registration:
        registration.status = new_status
        self.db.commit()
        self.db.refresh(registration)
        return registration

    @staticmethod
    def _uuid_advisory_lock_keys(value: UUID) -> tuple[int, int]:
        value_int = value.int
        high = (value_int >> 96) & 0xFFFFFFFF
        low = value_int & 0xFFFFFFFF
        return RegistrationRepository._to_signed_int32(
            high
        ), RegistrationRepository._to_signed_int32(low)

    @staticmethod
    def _to_signed_int32(value: int) -> int:
        if value >= 0x80000000:
            return value - 0x100000000
        return value


def get_registration_repository(
    db: Session = Depends(get_db),
) -> Generator[RegistrationRepository, None, None]:
    yield RegistrationRepository(db)
