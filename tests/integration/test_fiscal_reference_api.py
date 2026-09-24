from datetime import date, datetime, timedelta

import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import (
    FiscalCountry,
    FiscalCustomsUnit,
    FiscalMunicipality,
    Organization,
    User,
)
from app.services.fiscal_reference import FiscalReferenceService


class TestConfig:
    TESTING = True
    SECRET_KEY = "fiscal-reference-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False


@pytest.fixture
def api():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        organization = Organization(nome="Organização Teste", slug="org-referencia")
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            nome="Operador Teste",
            email="referencia@example.invalid",
            role="operacao",
            ativo=True,
        )
        user.set_password("test-password")
        db.session.add_all(
            [
                user,
                FiscalMunicipality(
                    code="3550308",
                    name="São Paulo",
                    state="SP",
                    active=True,
                    updated_at=datetime.utcnow(),
                ),
                FiscalMunicipality(
                    code="4106902",
                    name="Curitiba",
                    state="PR",
                    active=True,
                    updated_at=datetime.utcnow(),
                ),
                FiscalMunicipality(
                    code="9999999",
                    name="Município desativado",
                    state="PR",
                    active=False,
                    updated_at=datetime.utcnow(),
                ),
                FiscalCountry(
                    bacen_code="1600",
                    iso_alpha_2="CN",
                    iso_alpha_3="CHN",
                    name="China",
                    valid_from=date(2000, 1, 1),
                    active=True,
                    updated_at=datetime.utcnow(),
                ),
                FiscalCountry(
                    bacen_code="9998",
                    iso_alpha_2="ZZ",
                    iso_alpha_3="ZZZ",
                    name="País expirado",
                    valid_until=date(2010, 12, 31),
                    active=True,
                    updated_at=datetime.utcnow(),
                ),
            ]
        )
        db.session.commit()

        now = datetime.utcnow()
        token = jwt.encode(
            {
                "sub": str(user.id),
                "email": user.email,
                "role": user.role,
                "principal_type": "user",
                "type": "access",
                "iat": now,
                "exp": now + timedelta(hours=1),
            },
            app.config["SECRET_KEY"],
            algorithm="HS256",
        )
        yield app.test_client(), {"Authorization": f"Bearer {token}"}
        db.session.remove()
        db.drop_all()


def test_search_municipalities_is_accent_insensitive_and_filters_state(api):
    client, headers = api
    response = client.get(
        "/fiscal-reference/municipalities?q=sao&state=sp",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["state"] == "SP"
    assert body["items"] == [
        {
            "active": True,
            "code": "3550308",
            "name": "São Paulo",
            "state": "SP",
            "updated_at": body["items"][0]["updated_at"],
        }
    ]


def test_search_municipalities_accepts_ibge_code(api):
    client, headers = api
    response = client.get(
        "/fiscal-reference/municipalities?q=4106902",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["items"][0]["name"] == "Curitiba"


def test_postal_code_lookup_returns_normalized_address(api, monkeypatch):
    client, headers = api
    expected = {
        "zip_code": "80050530",
        "street": "Rua XV de Novembro",
        "complement": "",
        "district": "Centro",
        "city_code": "4106902",
        "city_name": "Curitiba",
        "state": "PR",
        "country_code": "1058",
        "country_name": "Brasil",
        "provider": "viacep",
        "cache_hit": False,
        "stale": False,
        "fetched_at": "2026-09-18T16:45:00",
    }

    class FakeService:
        def lookup(self, zip_code):
            assert zip_code == "80050530"
            return expected

    monkeypatch.setattr(
        "app.routes.fiscal_reference_routes._postal_code_service",
        lambda: FakeService(),
    )

    response = client.get(
        "/fiscal-reference/postal-codes/80050530",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json() == expected


def test_search_countries_respects_nfe_emission_date(api):
    client, headers = api
    current = client.get(
        "/fiscal-reference/countries?q=china&active_on=2026-08-25",
        headers=headers,
    )
    expired = client.get(
        "/fiscal-reference/countries?q=expirado&active_on=2026-08-25",
        headers=headers,
    )
    historical = client.get(
        "/fiscal-reference/countries?q=expirado&active_on=2010-01-01",
        headers=headers,
    )

    assert current.status_code == 200
    assert current.get_json()["items"][0]["bacen_code"] == "1600"
    assert expired.get_json()["items"] == []
    assert historical.get_json()["items"][0]["bacen_code"] == "9998"


def test_reference_endpoints_require_authentication(api):
    client, _ = api

    assert client.get("/fiscal-reference/municipalities?q=curitiba").status_code == 401
    assert client.get("/fiscal-reference/countries?q=china").status_code == 401
    assert client.get("/fiscal-reference/postal-codes/80050530").status_code == 401


def test_country_search_rejects_invalid_active_on(api):
    client, headers = api
    response = client.get(
        "/fiscal-reference/countries?q=china&active_on=25-08-2026",
        headers=headers,
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"


def test_tabx_customs_unit_is_cached_and_reused(api):
    _client, _headers = api
    from flask import current_app

    with current_app.app_context():
        item = FiscalReferenceService.cache_customs_unit_from_tabx(
            code="0927800",
            payload={
                "dados": [
                    {
                        "campos": [
                            {"nome": "CODIGO", "valor": "0927800"},
                            {"nome": "NOME", "valor": "ALF/PORTO DE ITAJAI"},
                            {"nome": "UF", "valor": "SC"},
                            {"nome": "CODIGO_MUNICIPIO", "valor": "4208203"},
                        ]
                    }
                ]
            },
        )
        db.session.commit()

        cached = FiscalReferenceService.find_customs_unit("0927800")
        assert cached is not None
        assert cached.description == "ALF/PORTO DE ITAJAI"
        assert cached.state == "SC"
        assert cached.municipality_code == "4208203"
        assert FiscalCustomsUnit.query.count() == 1


def test_tabx_country_is_cached_for_name_and_iso_search(api):
    _client, _headers = api
    from flask import current_app

    with current_app.app_context():
        FiscalReferenceService.cache_country_from_tabx(
            iso_alpha_2="US",
            payload={
                "dados": [
                    {
                        "campos": [
                            {"nome": "CODIGO", "valor": "2496"},
                            {"nome": "NOME", "valor": "Estados Unidos"},
                            {"nome": "SIGLA_ISO2", "valor": "US"},
                        ]
                    }
                ]
            },
        )
        db.session.commit()

        cached = FiscalReferenceService.find_country(iso_alpha_2="US")
        assert cached is not None
        assert cached.bacen_code == "2496"
        assert cached.name == "Estados Unidos"
