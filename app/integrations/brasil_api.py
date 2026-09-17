from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.cnpj import is_valid_cnpj, normalize_cnpj


class BrasilApiError(RuntimeError):
    """Erro base da consulta pública de CNPJ."""


class BrasilApiNotFoundError(BrasilApiError):
    """O CNPJ não foi encontrado pelo provedor."""


class BrasilApiUnavailableError(BrasilApiError):
    """O provedor não respondeu de forma utilizável."""


@dataclass(frozen=True)
class BrasilApiResponse:
    status_code: int
    payload: Any


class BrasilApiTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> BrasilApiResponse:
        ...


class UrllibBrasilApiTransport:
    def get(self, url: str, *, timeout_seconds: float) -> BrasilApiResponse:
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
                return BrasilApiResponse(
                    status_code=response.status,
                    payload=self._decode(response.read()),
                )
        except HTTPError as exc:
            return BrasilApiResponse(
                status_code=exc.code,
                payload=self._decode(exc.read()),
            )
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise BrasilApiUnavailableError(
                "Não foi possível consultar os dados públicos do CNPJ."
            ) from exc

    @staticmethod
    def _decode(body: bytes) -> Any:
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrasilApiUnavailableError(
                "O provedor de CNPJ retornou uma resposta inválida."
            ) from exc


class BrasilApiCnpjClient:
    def __init__(
        self,
        *,
        base_url: str = "https://brasilapi.com.br/api",
        timeout_seconds: float = 8,
        transport: BrasilApiTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrllibBrasilApiTransport()

    def lookup(self, cnpj: str) -> dict[str, Any]:
        normalized = normalize_cnpj(cnpj)
        if not is_valid_cnpj(normalized):
            raise ValueError(
                "CNPJ inválido. Informe 14 caracteres com verificadores válidos."
            )

        response = self.transport.get(
            f"{self.base_url}/cnpj/v1/{quote(normalized, safe='')}",
            timeout_seconds=self.timeout_seconds,
        )
        if response.status_code == 404:
            raise BrasilApiNotFoundError(
                "CNPJ não encontrado na base pública consultada."
            )
        if response.status_code == 400:
            raise ValueError("O provedor rejeitou o CNPJ informado.")
        if response.status_code == 429 or response.status_code >= 500:
            raise BrasilApiUnavailableError(
                "A consulta pública de CNPJ está temporariamente indisponível."
            )
        if response.status_code != 200 or not isinstance(response.payload, dict):
            raise BrasilApiUnavailableError(
                "O provedor de CNPJ retornou uma resposta inesperada."
            )

        return self._normalize(response.payload, fallback_cnpj=normalized)

    @classmethod
    def _normalize(
        cls,
        payload: dict[str, Any],
        *,
        fallback_cnpj: str,
    ) -> dict[str, Any]:
        principal = cls._activity(
            payload.get("cnae_fiscal"),
            payload.get("cnae_fiscal_descricao"),
        )
        secondary = [
            activity
            for item in payload.get("cnaes_secundarios") or []
            if isinstance(item, dict)
            if (
                activity := cls._activity(
                    item.get("codigo"),
                    item.get("descricao"),
                )
            )
        ]
        address = {
            "street_type": cls._text(payload.get("descricao_tipo_de_logradouro")),
            "street": cls._text(payload.get("logradouro")),
            "number": cls._text(payload.get("numero")),
            "complement": cls._text(payload.get("complemento")),
            "district": cls._text(payload.get("bairro")),
            "city": cls._text(payload.get("municipio")),
            "state": cls._text(payload.get("uf")),
            "zip_code": cls._digits(payload.get("cep")),
            "city_code": cls._digits(payload.get("codigo_municipio_ibge")),
        }
        simple = payload.get("opcao_pelo_simples") is True
        mei = payload.get("opcao_pelo_mei") is True

        return {
            "provider": "brasil_api",
            "cnpj": normalize_cnpj(payload.get("cnpj") or fallback_cnpj),
            "legal_name": cls._text(payload.get("razao_social")),
            "trade_name": cls._text(payload.get("nome_fantasia")),
            "registration_status": cls._text(
                payload.get("descricao_situacao_cadastral")
            ),
            "main_activity": principal,
            "secondary_activities": secondary,
            "address": address,
            "formatted_address": cls._formatted_address(address),
            "tax_regime_suggestion": "1" if simple or mei else None,
        }

    @classmethod
    def _activity(cls, code: Any, description: Any) -> dict[str, str] | None:
        normalized_code = cls._digits(code)
        normalized_description = cls._text(description)
        if not normalized_code and not normalized_description:
            return None
        return {
            "code": normalized_code.zfill(7) if normalized_code else "",
            "description": normalized_description,
        }

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _digits(value: Any) -> str:
        return "".join(character for character in str(value or "") if character.isdigit())

    @classmethod
    def _formatted_address(cls, address: dict[str, str]) -> str:
        street = " ".join(
            value
            for value in (address["street_type"], address["street"])
            if value
        )
        first_line = ", ".join(
            value
            for value in (street, address["number"], address["complement"])
            if value
        )
        city_state = "/".join(
            value for value in (address["city"], address["state"]) if value
        )
        return " — ".join(
            value
            for value in (
                first_line,
                address["district"],
                city_state,
                f"CEP {address['zip_code']}" if address["zip_code"] else "",
            )
            if value
        )
