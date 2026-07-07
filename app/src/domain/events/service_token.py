"""Token de serviço (OAuth2 client_credentials) para chamar o events-service.

O events-service (avengers) aplica autenticação global — Bearer obrigatório em
TODAS as rotas, inclusive `GET /events`. Como o T2 chama o events-service sem um
usuário no contexto (ex.: em `GET /events/available`, que é público), ele se
autentica como **serviço** no auth-service (0x_t1) via `POST /auth/token`
(grant_type=client_credentials) e reusa o token de máquina resultante.

O token é cacheado em memória por processo e renovado um pouco antes de expirar
(lendo o claim `exp` do JWT) ou sob demanda quando o events-service devolve 401.
"""

from __future__ import annotations

import base64
import json
import threading
import time

import httpx

from src.config import Settings

# expira_em - margem: renova antes de estourar para evitar corrida com o relógio.
_EXPIRY_SKEW_SECONDS = 60.0

_LOCK = threading.Lock()
# client_id -> (token, expires_at_epoch)
_CACHE: dict[str, tuple[str, float]] = {}


def _decode_exp(token: str) -> float | None:
    """Lê o `exp` do payload do JWT sem verificar a assinatura (só p/ cache)."""
    try:
        payload_segment = token.split(".")[1]
        payload_segment += "=" * (-len(payload_segment) % 4)  # padding base64url
        payload = json.loads(base64.urlsafe_b64decode(payload_segment))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _request_token(settings: Settings) -> str:
    data = {
        "grant_type": "client_credentials",
        "client_id": settings.EVENTS_SERVICE_CLIENT_ID,
        "client_secret": settings.EVENTS_SERVICE_CLIENT_SECRET,
    }
    if settings.EVENTS_SERVICE_CLIENT_SCOPE:
        data["scope"] = settings.EVENTS_SERVICE_CLIENT_SCOPE

    url = settings.AUTH_SERVICE_BASE_URL.rstrip("/") + "/auth/token"
    response = httpx.post(url, data=data, timeout=5.0)
    response.raise_for_status()
    return response.json()["access_token"]


def get_events_service_token(settings: Settings, *, force_refresh: bool = False) -> str:
    """Retorna um token de serviço válido, usando cache por client_id."""
    key = settings.EVENTS_SERVICE_CLIENT_ID
    now = time.time()
    with _LOCK:
        if not force_refresh:
            cached = _CACHE.get(key)
            if cached is not None and cached[1] - _EXPIRY_SKEW_SECONDS > now:
                return cached[0]

        token = _request_token(settings)
        expires_at = _decode_exp(token) or (now + 300.0)
        _CACHE[key] = (token, expires_at)
        return token


def reset_events_service_token_cache() -> None:
    """Limpa o cache (útil em testes)."""
    with _LOCK:
        _CACHE.clear()
