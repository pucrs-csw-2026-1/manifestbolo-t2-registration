"""HTTP controller for the registration endpoint."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.domain.auth.dependencies import (
    ensure_self_or_admin,
    ensure_self_or_manager_or_admin,
    get_current_user,
    is_admin,
    require_manager_or_admin,
)
from src.domain.auth.schemas import UserResponse
from src.domain.events.client import EventsClient, get_events_client

from .schemas import (
    ActivityRegistrationRequest,
    ActivityRegistrationResponse,
    AvailableEventResponse,
    CheckInStatusResponse,
    ConfirmationCodeRequest,
    ConfirmationResponse,
    EventActivityResponse,
    GuestRegistrationRequest,
    GuestRegistrationResponse,
    RegistrationCreateRequest,
    RegistrationResponse,
)
from .service import RegistrationService, get_registration_service

router = APIRouter(tags=["registration"])


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cria uma inscrição",
    description="Cria uma inscrição para o par usuário/evento informado.",
)
def register(
    body: RegistrationCreateRequest,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(get_current_user),
    events_client: EventsClient = Depends(get_events_client),
) -> RegistrationResponse:
    registration, confirmation = service.register(
        body.event_id,
        body.user_id,
        auth_user.id,
        events_client,
        allow_different_user=is_admin(auth_user),
    )
    return RegistrationResponse(
        eventId=registration.event_id,
        userId=registration.user_id,
        status=registration.status,
        createdAt=registration.created_at,
        updatedAt=registration.updated_at,
        confirmationId=confirmation.id,
        confirmationToken=confirmation.token,
    )


# ---------------------------------------------------------------------------
# GET /users/{user_id}/registrations – inscrições em eventos de um usuário
# ---------------------------------------------------------------------------


@router.get(
    "/users/{user_id}/registrations",
    response_model=list[GuestRegistrationResponse],
    status_code=status.HTTP_200_OK,
    summary="Lista as inscrições em eventos de um usuário",
    description=(
        "Retorna todas as inscrições em eventos do usuário informado (incluindo "
        "canceladas, para histórico). O próprio usuário pode consultar suas inscrições; "
        "managers e admins podem consultar as de qualquer usuário."
    ),
)
def list_user_registrations(
    user_id: UUID,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(get_current_user),
) -> list[GuestRegistrationResponse]:
    ensure_self_or_manager_or_admin(auth_user, user_id)
    registrations = service.list_user_registrations(user_id)
    return [
        GuestRegistrationResponse(
            eventId=r.event_id,
            userId=r.user_id,
            status=r.status,
            createdAt=r.created_at,
            updatedAt=r.updated_at,
        )
        for r in registrations
    ]


# ---------------------------------------------------------------------------
# GET /users/{user_id}/activities – inscrições em atividades de um usuário
# ---------------------------------------------------------------------------


@router.get(
    "/users/{user_id}/activities",
    response_model=list[ActivityRegistrationResponse],
    status_code=status.HTTP_200_OK,
    summary="Lista as inscrições em atividades (sub-áreas) de um usuário",
    description=(
        "Retorna todas as inscrições em atividades do usuário informado. "
        "O próprio usuário pode consultar suas inscrições; managers e admins "
        "podem consultar as de qualquer usuário."
    ),
)
def list_user_activity_registrations(
    user_id: UUID,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(get_current_user),
) -> list[ActivityRegistrationResponse]:
    ensure_self_or_manager_or_admin(auth_user, user_id)
    registrations = service.list_user_activity_registrations(user_id)
    return [
        ActivityRegistrationResponse(
            activityId=r.activity_id,
            userId=r.user_id,
            eventId=r.event_id,
            createdAt=r.created_at,
            updatedAt=r.updated_at,
        )
        for r in registrations
    ]


# ---------------------------------------------------------------------------
# GET /events/available – eventos com vagas disponíveis
# ---------------------------------------------------------------------------


@router.get(
    "/events/available",
    response_model=list[AvailableEventResponse],
    status_code=status.HTTP_200_OK,
    summary="Lista eventos disponíveis para inscrição",
    description=(
        "Consulta o microserviço de eventos para obter a lista de eventos cadastrados "
        "e aplica dois filtros antes de retornar: "
        "(1) remove eventos cuja data de encerramento já passou, ou seja, só eventos futuros ou em andamento são considerados; "
        "(2) remove eventos que já atingiram a capacidade máxima de inscritos, "
        "comparando o limite do evento com o total de inscrições registradas neste serviço. "
        "Retorna apenas os eventos que ainda estão dentro do prazo e possuem vagas disponíveis."
    ),
)
def list_available_events(
    service: RegistrationService = Depends(get_registration_service),
    events_client: EventsClient = Depends(get_events_client),
) -> list[AvailableEventResponse]:
    return service.list_available_events(events_client)


# ---------------------------------------------------------------------------
# GET /events/{event_id}/activities – atividades de um evento
# ---------------------------------------------------------------------------


@router.get(
    "/events/{event_id}/activities",
    response_model=list[EventActivityResponse],
    status_code=status.HTTP_200_OK,
    summary="Lista as atividades de um evento com a contagem de inscritos",
    description=(
        "Consulta o microserviço de eventos para obter as atividades (sub-áreas) do "
        "evento informado e cruza cada uma com o total de inscrições registradas neste "
        "serviço, calculando as vagas restantes quando a atividade possui capacidade "
        "máxima. Endpoint público: não exige autenticação do usuário; este serviço se "
        "autentica internamente como serviço no microserviço de eventos. "
        "Retorna 404 caso o evento não exista no microserviço de eventos."
    ),
    responses={
        404: {"description": "Evento não encontrado no microserviço de eventos"},
    },
)
def list_event_activities(
    event_id: UUID,
    service: RegistrationService = Depends(get_registration_service),
    events_client: EventsClient = Depends(get_events_client),
) -> list[EventActivityResponse]:
    return service.list_event_activities(event_id, events_client)


# ---------------------------------------------------------------------------
# GET /events/{event_id}/registrations – inscritos de um evento
# ---------------------------------------------------------------------------


@router.get(
    "/events/{event_id}/registrations",
    response_model=list[GuestRegistrationResponse],
    status_code=status.HTTP_200_OK,
    summary="Lista usuários registrados em um evento",
    description="Consulta no banco de dados deste serviço todos os convidados inscritos no evento informado.",
)
def list_event_registrations(
    event_id: UUID,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(require_manager_or_admin),
) -> list[GuestRegistrationResponse]:
    _ = auth_user
    registrations = service.list_event_registrations(event_id)
    return [
        GuestRegistrationResponse(
            eventId=r.event_id,
            userId=r.user_id,
            status=r.status,
            createdAt=r.created_at,
            updatedAt=r.updated_at,
        )
        for r in registrations
    ]


# ---------------------------------------------------------------------------
# GET /activities/{activity_id}/registrations – user ids inscritos numa atividade
# ---------------------------------------------------------------------------


@router.get(
    "/activities/{activity_id}/registrations",
    response_model=list[UUID],
    status_code=status.HTTP_200_OK,
    summary="Lista os ids de usuários inscritos em uma atividade",
    description=(
        "Retorna um array com os `userId` de todos os usuários inscritos na atividade "
        "informada. Quando a atividade não possui inscritos, retorna 200 com um array vazio."
    ),
)
def list_activity_registrations(
    activity_id: UUID,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(require_manager_or_admin),
) -> list[UUID]:
    _ = auth_user
    return service.list_activity_user_ids(activity_id)


# ---------------------------------------------------------------------------
# GET /activities/{activity_id}/users/{user_id} – inscrição em atividade
# ---------------------------------------------------------------------------


@router.get(
    "/activities/{activity_id}/users/{user_id}",
    response_model=ActivityRegistrationResponse,
    status_code=status.HTTP_200_OK,
    summary="Busca a inscrição de um usuário em uma atividade",
    description="Consulta no banco de dados deste serviço a inscrição informada por atividade e usuário.",
)
def get_activity_registration(
    activity_id: UUID,
    user_id: UUID,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(get_current_user),
) -> ActivityRegistrationResponse:
    ensure_self_or_manager_or_admin(auth_user, user_id)
    registration = service.get_activity_registration(activity_id, user_id)

    if registration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registration not found.",
        )

    return ActivityRegistrationResponse(
        activityId=registration.activity_id,
        userId=registration.user_id,
        eventId=registration.event_id,
        createdAt=registration.created_at,
        updatedAt=registration.updated_at,
    )


# ---------------------------------------------------------------------------
# POST /activities/registrations – inscrição em atividade
# ---------------------------------------------------------------------------


@router.post(
    "/activities/registrations",
    response_model=ActivityRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cria uma inscrição em atividade",
    description="Cria uma inscrição de usuário em uma atividade com base no payload informado.",
)
def register_activity(
    body: ActivityRegistrationRequest,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(get_current_user),
    events_client: EventsClient = Depends(get_events_client),
) -> ActivityRegistrationResponse:
    ensure_self_or_admin(auth_user, body.user_id)
    registration = service.register_activity(
        body.activity_id,
        body.user_id,
        body.event_id,
        events_client,
    )
    return ActivityRegistrationResponse(
        activityId=registration.activity_id,
        userId=registration.user_id,
        eventId=registration.event_id,
        createdAt=registration.created_at,
        updatedAt=registration.updated_at,
    )


# ---------------------------------------------------------------------------
# POST /events/{event_id}/guests – inscrição de convidado
# ---------------------------------------------------------------------------


@router.post(
    "/events/{event_id}/guests",
    response_model=GuestRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Inscreve um convidado em um evento",
    description=(
        "Registra um usuário como convidado no evento especificado. "
        "Deve verificar se ainda há vagas disponíveis antes de criar a inscrição."
    ),
)
def register_guest(
    event_id: UUID,
    body: GuestRegistrationRequest,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(get_current_user),
    events_client: EventsClient = Depends(get_events_client),
) -> GuestRegistrationResponse:
    registration, confirmation = service.register(
        event_id,
        body.user_id,
        auth_user.id,
        events_client,
        allow_different_user=is_admin(auth_user),
    )
    return GuestRegistrationResponse(
        event_id=registration.event_id,
        user_id=registration.user_id,
        status=registration.status,
        created_at=registration.created_at,
        updated_at=registration.updated_at,
        confirmation_id=confirmation.id,
        confirmation_token=confirmation.token,
    )


# ---------------------------------------------------------------------------
# DELETE /events/{event_id}/guests/{user_id} – cancelamento de inscrição
# ---------------------------------------------------------------------------


@router.delete(
    "/events/{event_id}/guests/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancela a inscrição de um convidado em um evento",
    description=(
        "Remove a inscrição do usuário informado no evento especificado. "
        "Só é possível cancelar inscrições em eventos que ainda não ocorreram; "
        "tentativas de cancelamento após a data do evento devem ser rejeitadas com 422. "
        "Retorna 204 No Content em caso de sucesso e 404 caso a inscrição não exista."
    ),
)
def cancel_guest_registration(
    event_id: UUID,
    user_id: UUID,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(get_current_user),
) -> None:
    ensure_self_or_admin(auth_user, user_id)
    service.cancel_registration(event_id, user_id)


# ---------------------------------------------------------------------------
# GET /events/{event_id}/guests/{user_id}/check-in – validação para check-in
# ---------------------------------------------------------------------------


@router.get(
    "/events/{event_id}/guests/{user_id}/check-in",
    response_model=CheckInStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Valida se um usuário está inscrito em um evento (uso interno: check-in)",
    description=(
        "Endpoint destinado ao microsserviço de check-in. "
        "Verifica se o usuário informado possui uma inscrição ativa no evento especificado "
        "e se essa inscrição foi confirmada. "
        "Retorna sempre 200 com o campo `status` indicando o resultado — "
        "o serviço chamador é responsável por decidir se permite ou nega o acesso físico ao evento."
    ),
    tags=["registration", "check-in"],
)
def validate_check_in(
    event_id: UUID,
    user_id: UUID,
    service: RegistrationService = Depends(get_registration_service),
    auth_user: UserResponse = Depends(require_manager_or_admin),
) -> CheckInStatusResponse:
    _ = auth_user
    registration = service.get_check_in_registration(event_id, user_id)

    if registration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registration not found.",
        )

    return CheckInStatusResponse(
        eventId=event_id,
        userId=user_id,
        status=registration.status,
        createdAt=registration.created_at,
        updatedAt=registration.updated_at,
    )


# ---------------------------------------------------------------------------
# POST /events/confirmation/{confirmation_id} – confirmação de inscrição
# ---------------------------------------------------------------------------


@router.post(
    "/events/confirmation/{confirmation_id}",
    response_model=ConfirmationResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirma a inscrição de um usuário em um evento",
    description=(
        "Valida o token alfanumérico enviado pelo usuário para confirmar sua inscrição. "
        "O `confirmation_id` identifica o registro de token de validação do domínio de registration "
        "armazenado na tabela auxiliar `authentication_tokens`. "
        "O token deve ter exatamente 8 caracteres alfanuméricos (gerado automaticamente pelo backend). "
        "\n\n**Erros tratados:**\n"
        "- `404 Not Found`: `confirmation_id` não existe na tabela.\n"
        "- `400 Bad Request`: token informado está incorreto.\n"
        "- `410 Gone`: o código expirou (`expires_at` ultrapassado).\n"
        "- `409 Conflict`: a inscrição já foi confirmada anteriormente (`status = CONFIRMED`).\n"
        "- `422 Unprocessable Entity`: campo `token` ausente ou fora do formato esperado (validado pelo Pydantic)."
    ),
    responses={
        400: {"description": "Token incorreto"},
        404: {"description": "confirmation_id não encontrado"},
        409: {"description": "Inscrição já confirmada anteriormente"},
        410: {"description": "Token expirado"},
    },
)
def confirm_registration(
    confirmation_id: UUID,
    body: ConfirmationCodeRequest,
    service: RegistrationService = Depends(get_registration_service),
) -> ConfirmationResponse:
    registration = service.confirm(confirmation_id, body.token)
    return ConfirmationResponse(
        confirmation_id=confirmation_id,
        event_id=registration.event_id,
        user_id=registration.user_id,
        confirmed_at=registration.updated_at or datetime.now(UTC),
    )
