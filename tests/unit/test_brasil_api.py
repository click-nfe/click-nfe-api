from dataclasses import dataclass
from typing import Any

import pytest

from app.integrations.brasil_api import (
    BrasilApiCnpjClient,
    BrasilApiNotFoundError,
    BrasilApiResponse,
    BrasilApiUnavailableError,
)


@dataclass
class FakeTransport:
    response: BrasilApiResponse

    def __post_init__(self):
        self.requests: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout_seconds: float) -> BrasilApiResponse:
        self.requests.append((url, timeout_seconds))
        return self.response


def test_lookup_normalizes_company_fields_for_client_form():
    transport = FakeTransport(
        BrasilApiResponse(
            200,
            {
                "cnpj": "08266216000105",
                "razao_social": "GUIMARAES & SARDINHA LTDA",
                "nome_fantasia": "VITTORIA WHEELS",
                "descricao_situacao_cadastral": "ATIVA",
                "cnae_fiscal": 4530703,
                "cnae_fiscal_descricao": "Comércio a varejo de peças para veículos",
                "cnaes_secundarios": [
                    {
                        "codigo": 4520001,
                        "descricao": "Serviços de manutenção de veículos",
                    }
                ],
                "descricao_tipo_de_logradouro": "RUA",
                "logradouro": "DAS FLORES",
                "numero": "10",
                "complemento": "SALA 2",
                "bairro": "CENTRO",
                "municipio": "CURITIBA",
                "uf": "PR",
                "cep": "80000000",
                "codigo_municipio_ibge": 4106902,
                "opcao_pelo_simples": True,
                "opcao_pelo_mei": False,
            },
        )
    )
    client = BrasilApiCnpjClient(
        base_url="https://example.invalid/api/",
        timeout_seconds=4,
        transport=transport,
    )

    result = client.lookup("08.266.216/0001-05")

    assert transport.requests == [
        ("https://example.invalid/api/cnpj/v1/08266216000105", 4)
    ]
    assert result == {
        "provider": "brasil_api",
        "cnpj": "08266216000105",
        "legal_name": "GUIMARAES & SARDINHA LTDA",
        "trade_name": "VITTORIA WHEELS",
        "registration_status": "ATIVA",
        "main_activity": {
            "code": "4530703",
            "description": "Comércio a varejo de peças para veículos",
        },
        "secondary_activities": [
            {
                "code": "4520001",
                "description": "Serviços de manutenção de veículos",
            }
        ],
        "address": {
            "street_type": "RUA",
            "street": "DAS FLORES",
            "number": "10",
            "complement": "SALA 2",
            "district": "CENTRO",
            "city": "CURITIBA",
            "state": "PR",
            "zip_code": "80000000",
            "city_code": "4106902",
        },
        "formatted_address": (
            "RUA DAS FLORES, 10, SALA 2 — CENTRO — "
            "CURITIBA/PR — CEP 80000000"
        ),
        "tax_regime_suggestion": "1",
    }


def test_lookup_does_not_infer_normal_tax_regime_from_non_simples_company():
    transport = FakeTransport(
        BrasilApiResponse(
            200,
            {
                "cnpj": "03114340000131",
                "razao_social": "ORDEMILK LTDA.",
                "opcao_pelo_simples": False,
                "opcao_pelo_mei": False,
            },
        )
    )

    result = BrasilApiCnpjClient(transport=transport).lookup(
        "03.114.340/0001-31"
    )

    assert result["tax_regime_suggestion"] is None


def test_lookup_rejects_invalid_cnpj_without_calling_provider():
    transport = FakeTransport(BrasilApiResponse(200, {}))

    with pytest.raises(ValueError, match="CNPJ inválido"):
        BrasilApiCnpjClient(transport=transport).lookup("03.114.340/0001-30")

    assert transport.requests == []


@pytest.mark.parametrize(
    ("status_code", "exception_type"),
    [
        (404, BrasilApiNotFoundError),
        (429, BrasilApiUnavailableError),
        (500, BrasilApiUnavailableError),
    ],
)
def test_lookup_maps_provider_failures(status_code, exception_type):
    transport = FakeTransport(BrasilApiResponse(status_code, {}))

    with pytest.raises(exception_type):
        BrasilApiCnpjClient(transport=transport).lookup(
            "03.114.340/0001-31"
        )
