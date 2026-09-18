from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class PostalCodeProviderError(RuntimeError):
    """O provedor de CEP não respondeu de forma utilizável."""


class PostalCodeNotFoundError(RuntimeError):
    """O CEP não existe no provedor consultado."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    payload: Any


class HttpTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        ...


class UrllibHttpTransport:
    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "click-nfe-api/1.0",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.status,
                    payload=self._decode(response.read()),
                )
        except HTTPError as exc:
            return HttpResponse(
                status_code=exc.code,
                payload=self._decode(exc.read()),
            )
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise PostalCodeProviderError(
                "O provedor de CEP está temporariamente indisponível."
            ) from exc

    @staticmethod
    def _decode(body: bytes) -> Any:
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PostalCodeProviderError(
                "O provedor de CEP retornou uma resposta inválida."
            ) from exc


class PostalCodeProvider(Protocol):
    name: str

    def lookup(self, zip_code: str) -> dict[str, str]:
        ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _digits(value: Any) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


class ViaCepProvider:
    name = "viacep"

    def __init__(
        self,
        *,
        base_url: str = "https://viacep.com.br",
        timeout_seconds: float = 5,
        transport: HttpTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrllibHttpTransport()

    def lookup(self, zip_code: str) -> dict[str, str]:
        response = self.transport.get(
            f"{self.base_url}/ws/{quote(zip_code, safe='')}/json/",
            timeout_seconds=self.timeout_seconds,
        )
        if response.status_code == 404:
            raise PostalCodeNotFoundError("CEP não encontrado.")
        if response.status_code != 200 or not isinstance(response.payload, dict):
            raise PostalCodeProviderError("O ViaCEP retornou uma resposta inesperada.")
        if response.payload.get("erro") is True:
            raise PostalCodeNotFoundError("CEP não encontrado.")

        return {
            "zip_code": _digits(response.payload.get("cep")) or zip_code,
            "street": _text(response.payload.get("logradouro")),
            "complement": _text(response.payload.get("complemento")),
            "district": _text(response.payload.get("bairro")),
            "city_code": _digits(response.payload.get("ibge")),
            "city_name": _text(response.payload.get("localidade")),
            "state": _text(response.payload.get("uf")).upper(),
        }


class BrasilApiCepProvider:
    name = "brasil_api"

    def __init__(
        self,
        *,
        base_url: str = "https://brasilapi.com.br/api",
        timeout_seconds: float = 5,
        transport: HttpTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrllibHttpTransport()

    def lookup(self, zip_code: str) -> dict[str, str]:
        response = self.transport.get(
            f"{self.base_url}/cep/v2/{quote(zip_code, safe='')}",
            timeout_seconds=self.timeout_seconds,
        )
        if response.status_code == 404:
            raise PostalCodeNotFoundError("CEP não encontrado.")
        if response.status_code != 200 or not isinstance(response.payload, dict):
            raise PostalCodeProviderError(
                "A BrasilAPI retornou uma resposta inesperada."
            )

        return {
            "zip_code": _digits(response.payload.get("cep")) or zip_code,
            "street": _text(response.payload.get("street")),
            "complement": "",
            "district": _text(response.payload.get("neighborhood")),
            "city_code": "",
            "city_name": _text(response.payload.get("city")),
            "state": _text(response.payload.get("state")).upper(),
        }
