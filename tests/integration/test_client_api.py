from datetime import datetime, timedelta

import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import Organization, User
from app.integrations.brasil_api import BrasilApiUnavailableError


class TestConfig:
    TESTING = True
    SECRET_KEY = "client-api-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


@pytest.fixture
def api():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        organization = Organization(nome="Organização Teste", slug="org-teste")
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            nome="Operador Teste",
            email="operador-clientes@example.invalid",
            role="admin",
            ativo=True,
        )
        user.set_password("test-password")
        db.session.add(user)
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


def test_create_client_normalizes_cnpj_and_assigns_organization(api):
    client, headers = api

    response = client.post(
        "/clients",
        headers=headers,
        json={
            "cnpj": "08.266.216/0001-05",
            "razao_social": "  GUIMARAES & SARDINHA LTDA  ",
            "nome_resumido": "  VITTORIA WHEELS  ",
            "regime_tributacao": "LUCRO_PRESUMIDO_OU_REAL",
        },
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["cnpj"] == "08266216000105"
    assert body["razao_social"] == "GUIMARAES & SARDINHA LTDA"
    assert body["nome_resumido"] == "VITTORIA WHEELS"
    assert body["organization_id"] is not None
    assert body["ativo"] is True


def test_lookup_cnpj_requires_authentication(api):
    client, _headers = api

    response = client.get("/clients/lookup/cnpj/03114340000131")

    assert response.status_code == 401


def test_lookup_cnpj_returns_normalized_public_data(api, monkeypatch):
    client, headers = api
    expected = {
        "provider": "brasil_api",
        "cnpj": "03114340000131",
        "legal_name": "ORDEMILK LTDA.",
        "trade_name": "ORDEMILK",
        "registration_status": "ATIVA",
        "main_activity": {
            "code": "1051100",
            "description": "Preparação do leite",
        },
        "secondary_activities": [],
        "address": {},
        "formatted_address": "RUA EXEMPLO, 10 — CENTRO — TREZE TILIAS/SC",
        "tax_regime_suggestion": None,
    }
    monkeypatch.setattr(
        "app.routes.client_routes.BrasilApiCnpjClient.lookup",
        lambda self, cnpj: expected,
    )

    response = client.get(
        "/clients/lookup/cnpj/03114340000131",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json() == expected


def test_lookup_cnpj_reports_provider_unavailability(api, monkeypatch):
    client, headers = api

    def unavailable(self, cnpj):
        raise BrasilApiUnavailableError("Consulta temporariamente indisponível.")

    monkeypatch.setattr(
        "app.routes.client_routes.BrasilApiCnpjClient.lookup",
        unavailable,
    )

    response = client.get(
        "/clients/lookup/cnpj/03114340000131",
        headers=headers,
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "company_lookup_unavailable",
        "message": "Consulta temporariamente indisponível.",
    }


def test_create_client_returns_existing_id_for_duplicate(api):
    client, headers = api
    payload = {
        "cnpj": "03.114.340/0001-31",
        "razao_social": "ORDEMILK LTDA.",
    }

    created = client.post("/clients", headers=headers, json=payload)
    duplicate = client.post("/clients", headers=headers, json=payload)

    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.get_json() == {
        "error": "client_already_exists",
        "message": "Já existe um cliente com este CNPJ na organização.",
        "client_id": created.get_json()["id"],
    }


def test_create_client_rejects_invalid_cnpj(api):
    client, headers = api

    response = client.post(
        "/clients",
        headers=headers,
        json={"cnpj": "03.114.340/0001-30", "razao_social": "Cliente Inválido"},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "validation_error"
    assert "cnpj" in body["messages"]


def test_create_alphanumeric_client_and_fiscal_profile(api):
    client, headers = api

    created = client.post(
        "/clients",
        headers=headers,
        json={
            "cnpj": "12.ABC.345/01DE-35",
            "razao_social": "Cliente Alfanumérico Ltda",
        },
    )
    assert created.status_code == 201
    client_id = created.get_json()["id"]

    profile = client.put(
        f"/clients/{client_id}/fiscal-profile",
        headers=headers,
        json={
            "legal_name": "Cliente Alfanumérico Ltda",
            "cnpj": "12.ABC.345/01DE-35",
            "state_registration": "123456789",
            "tax_regime": "3",
            "street": "Rua de Teste",
            "number": "100",
            "district": "Centro",
            "city_code": "4106902",
            "city_name": "Curitiba",
            "state": "PR",
            "zip_code": "80000000",
        },
    )

    assert profile.status_code == 200
    assert profile.get_json()["cnpj"] == "12ABC34501DE35"


def test_fiscal_profile_rejects_cnpj_from_another_client(api):
    client, headers = api
    created = client.post(
        "/clients",
        headers=headers,
        json={
            "cnpj": "03.114.340/0001-31",
            "razao_social": "ORDEMILK LTDA.",
        },
    )
    assert created.status_code == 201

    profile = client.put(
        f"/clients/{created.get_json()['id']}/fiscal-profile",
        headers=headers,
        json={
            "legal_name": "Empresa de Outro CNPJ Ltda",
            "cnpj": "08.266.216/0001-05",
            "tax_regime": "3",
            "street": "Rua de Teste",
            "number": "100",
            "district": "Centro",
            "city_code": "4106902",
            "city_name": "Curitiba",
            "state": "PR",
            "zip_code": "80000000",
        },
    )

    assert profile.status_code == 400
    assert profile.get_json()["message"] == (
        "O CNPJ do perfil fiscal deve ser igual ao CNPJ do cliente."
    )


def test_client_list_does_not_expose_legacy_scope_metadata(api):
    client, headers = api
    created = client.post(
        "/clients",
        headers=headers,
        json={
            "cnpj": "03.114.340/0001-31",
            "razao_social": "ORDEMILK LTDA.",
        },
    )
    assert created.status_code == 201

    item = client.get("/clients", headers=headers).get_json()["items"][0]
    assert item["id"] == created.get_json()["id"]
    assert "scope_id" not in item
    assert "has_scope" not in item


def test_organization_response_does_not_expose_casco_fixed_info(api):
    client, headers = api

    response = client.get("/organizations/me", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["organization"]["slug"] == "org-teste"
    assert "fixedInfo" not in body


@pytest.mark.parametrize(
    "path",
    [
        "/scopes",
        "/prepostos",
        "/dashboards/admin",
        "/admin/settings",
        "/organizations/me/settings",
    ],
)
def test_legacy_routes_are_not_registered(api, path):
    client, headers = api

    response = client.get(path, headers=headers)

    assert response.status_code == 404
