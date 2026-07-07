"""HTTP client for the Events service."""

from collections.abc import Callable
import logging
from typing import Any, TypeVar
from uuid import UUID

from fastapi import Depends, HTTPException, status
import httpx
from pydantic import BaseModel

from src.config import Settings, get_settings

from .service_token import get_events_service_token

from .schemas import (
    ActivityResponse,
    EventListResponse,
    EventResponse,
    EventRoleResponse,
    EventsMetricsResponse,
)

logger = logging.getLogger(__name__)

EVENTS_UNAVAILABLE_DETAIL = "Events service unavailable"
EVENTS_REJECTED_DETAIL = "Events service rejected request"
NOT_FOUND_STATUS = status.HTTP_404_NOT_FOUND

ModelT = TypeVar("ModelT", bound=BaseModel)


class EventsClient:
    """Encapsulates calls to the Events service."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        settings: Settings | None = None,
        token_provider: Callable[..., str] | None = None,
    ) -> None:
        app_settings = settings or get_settings()
        self._settings = app_settings
        self._base_url = base_url or app_settings.EVENTS_SERVICE_BASE_URL
        self._timeout = timeout
        self._transport = transport
        # Injetável para testes; em produção usa o token de serviço real.
        self._token_provider = token_provider

    def _fetch_token(self, *, force_refresh: bool = False) -> str:
        if self._token_provider is not None:
            return self._token_provider(force_refresh=force_refresh)
        return get_events_service_token(self._settings, force_refresh=force_refresh)

    def list_events(self, page: int = 1, limit: int = 20) -> EventListResponse:
        response = self._request(
            "GET",
            "/events",
            params={"page": page, "limit": limit},
        )
        return self._parse_response(response, EventListResponse)

    def get_all_events(self, limit: int = 100) -> list[EventResponse]:
        """Return all events by following the Events service pagination."""

        events: list[EventResponse] = []
        page = 1

        while True:
            result = self.list_events(page=page, limit=limit)
            events.extend(result.data)

            if len(events) >= result.total or not result.data:
                return events

            page += 1

    def get_event_by_id(self, event_id: str | UUID) -> EventResponse | None:
        response = self._request(
            "GET", self._event_path(event_id), allow_not_found=True
        )
        if response.status_code == NOT_FOUND_STATUS:
            return None
        return self._parse_response(response, EventResponse)

    def get_events_metrics(self) -> EventsMetricsResponse:
        response = self._request("GET", "/events/metrics")
        return self._parse_response(response, EventsMetricsResponse)

    def list_event_activities(self, event_id: str | UUID) -> list[ActivityResponse]:
        response = self._request("GET", f"{self._event_path(event_id)}/activitys")
        return self._parse_response_list(response, ActivityResponse)

    def list_event_activitys(self, event_id: str | UUID) -> list[ActivityResponse]:
        """Compatibility alias for the Events service route spelling."""

        return self.list_event_activities(event_id)

    def list_event_roles(self, event_id: str | UUID) -> list[EventRoleResponse]:
        response = self._request("GET", f"{self._event_path(event_id)}/roles")
        return self._parse_response_list(response, EventRoleResponse)

    def _request(
        self,
        method: str,
        path: str,
        *,
        allow_not_found: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        response = self._send(method, path, force_refresh=False, **kwargs)
        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            # Token expirado/rotacionado — renova e tenta uma única vez.
            response = self._send(method, path, force_refresh=True, **kwargs)

        self._raise_for_error(response, allow_not_found=allow_not_found)
        return response

    def _send(
        self,
        method: str,
        path: str,
        *,
        force_refresh: bool,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            token = self._fetch_token(force_refresh=force_refresh)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("Failed to obtain events-service token: %s", exc)
            raise self._service_unavailable() from exc

        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                return client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            logger.warning("Events service timeout on %s %s", method, path)
            raise self._service_unavailable() from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Events service request failed on %s %s: %s", method, path, exc
            )
            raise self._service_unavailable() from exc

    @staticmethod
    def _parse_response(response: httpx.Response, model: type[ModelT]) -> ModelT:
        return model.model_validate(response.json())

    @staticmethod
    def _parse_response_list(
        response: httpx.Response,
        model: type[ModelT],
    ) -> list[ModelT]:
        return [model.model_validate(item) for item in response.json()]

    @staticmethod
    def _event_path(event_id: str | UUID) -> str:
        return f"/events/{event_id}"

    @staticmethod
    def _raise_for_error(
        response: httpx.Response,
        *,
        allow_not_found: bool,
    ) -> None:
        if allow_not_found and response.status_code == NOT_FOUND_STATUS:
            return

        if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            raise EventsClient._service_unavailable()

        if response.status_code >= status.HTTP_400_BAD_REQUEST:
            raise HTTPException(
                status_code=response.status_code,
                detail=EVENTS_REJECTED_DETAIL,
            )

    @staticmethod
    def _service_unavailable() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=EVENTS_UNAVAILABLE_DETAIL,
        )


def get_events_client(
    settings: Settings = Depends(get_settings),
) -> EventsClient:
    """Return an Events client instance for FastAPI dependency injection."""

    return EventsClient(settings=settings)
