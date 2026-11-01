"""Consulta sem credenciais para distinguir manutenção do Portal Único de falhas locais."""
from __future__ import annotations

import socket
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PROBE_URL = "https://portalunico.siscomex.gov.br/portal/"
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
    # A página pública aceita GET; a rota /api/autenticar/chave-acesso exige POST.
    request = Request(PROBE_URL, headers={"Accept": "text/html", "User-Agent": "click-nfe/1.0"})
    try:
        with urlopen(request, timeout=3) as response:
            body = response.read(8192)
            if is_maintenance_page(response.geturl(), body):
                return "unavailable"
            parts = urlsplit(response.geturl())
            official_portal = (parts.scheme == "https" and parts.hostname == "portalunico.siscomex.gov.br"
                               and parts.path.startswith("/portal/"))
            return "available" if response.status == 200 and official_portal else "unknown"
    except HTTPError as exc:
        body = exc.read(8192)
        if is_maintenance_page(exc.geturl(), body) or exc.code in {502, 503, 504}:
            return "unavailable"
        # Erros de validação/autenticação não devem definir o estado global do serviço.
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
            "available": "O site do Portal Único respondeu à verificação de disponibilidade.",
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
