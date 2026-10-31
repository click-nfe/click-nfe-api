"""Consulta sem credenciais para distinguir manutenção do Portal Único de falhas locais."""
from __future__ import annotations

import socket
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PROBE_URL = "https://portalunico.siscomex.gov.br/portal/api/autenticar/chave-acesso"
CACHE_SECONDS = 90
_cache: tuple[float, dict[str, str]] | None = None
_lock = Lock()


def is_maintenance_page(url: str, body: bytes) -> bool:
    """Reconhece apenas a página oficial de manutenção, sem considerar um erro comum como queda."""
    parts = urlsplit(url)
    if parts.hostname == "portalunico.siscomex.gov.br" and parts.path.lower().endswith("/manutencao.html"):
        return True
    text = body[:8192].decode("utf-8", errors="ignore").lower()
    return "portal único" in text and "manutenção programada" in text


def _probe() -> str:
    # GET sem Client-Id/Secret: nunca consome uma sessão nem testa a chave da organização.
    request = Request(PROBE_URL, headers={"Accept": "application/json", "User-Agent": "click-nfe/1.0"})
    try:
        with urlopen(request, timeout=3) as response:
            body = response.read(8192)
            if is_maintenance_page(response.geturl(), body):
                return "unavailable"
            content_type = response.headers.get("Content-Type", "").lower()
            return "available" if response.status == 200 and "json" in content_type else "unknown"
    except HTTPError as exc:
        body = exc.read(8192)
        if is_maintenance_page(exc.geturl(), body) or exc.code in {502, 503, 504}:
            return "unavailable"
        content_type = exc.headers.get("Content-Type", "").lower()
        if exc.code in {400, 401, 403, 405, 422} and "json" in content_type:
            return "available"
        return "unknown"
    except (URLError, TimeoutError, socket.timeout, OSError):
        # Uma falha da rede da nossa própria aplicação não prova que o Portal caiu.
        return "unknown"


def portal_unico_availability() -> dict[str, str]:
    global _cache
    with _lock:
        now = monotonic()
        if _cache is not None and now - _cache[0] < CACHE_SECONDS:
            return dict(_cache[1])
        state = _probe()
        messages = {
            "available": "O Portal Único respondeu à verificação de disponibilidade. A chave da organização é validada separadamente.",
            "unavailable": "O Portal Único sinalizou manutenção ou indisponibilidade. Consulte o serviço e tente novamente mais tarde.",
            "unknown": "Não foi possível confirmar a disponibilidade do Portal Único. Verifique a conexão e tente novamente.",
        }
        result = {
            "state": state,
            "message": messages[state],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        _cache = (monotonic(), result)
        return dict(result)
