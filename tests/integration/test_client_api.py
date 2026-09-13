from datetime import datetime, timedelta

import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import Organization, User


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


def test_create_client_normalizes_cnpj_and_scopes_organization(api):
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


def test_client_list_exposes_scope_metadata(api):
    client, headers = api
    created_client = client.post(
        "/clients",
        headers=headers,
        json={
            "cnpj": "03.114.340/0001-31",
            "razao_social": "ORDEMILK LTDA.",
        },
    ).get_json()

    before_scope = client.get("/clients", headers=headers).get_json()["items"][0]
    assert before_scope["scope_id"] is None
    assert before_scope["has_scope"] is False

    created_scope = client.post(
        f"/scopes?clientId={created_client['id']}",
        headers=headers,
        json={},
    )
    assert created_scope.status_code == 201

    after_scope = client.get("/clients", headers=headers).get_json()["items"][0]
    assert after_scope["scope_id"] == created_scope.get_json()["id"]
    assert after_scope["has_scope"] is True


def test_create_scope_for_client_rejects_second_scope(api):
    client, headers = api
    created_client = client.post(
        "/clients",
        headers=headers,
        json={
            "cnpj": "03.114.340/0001-31",
            "razao_social": "ORDEMILK LTDA.",
        },
    ).get_json()

    first_scope = client.post(
        f"/scopes?clientId={created_client['id']}",
        headers=headers,
        json={},
    )
    second_scope = client.post(
        f"/scopes?clientId={created_client['id']}",
        headers=headers,
        json={},
    )

    assert first_scope.status_code == 201
    assert second_scope.status_code == 409
    assert second_scope.get_json() == {
        "error": "client_scope_already_exists",
        "message": "Este cliente já possui um escopo.",
        "client_id": created_client["id"],
        "scope_id": first_scope.get_json()["id"],
    }


def test_multiple_scopes_without_client_remain_allowed(api):
    client, headers = api

    first_scope = client.post("/scopes", headers=headers, json={})
    second_scope = client.post("/scopes", headers=headers, json={})

    assert first_scope.status_code == 201
    assert second_scope.status_code == 201
    assert first_scope.get_json()["id"] != second_scope.get_json()["id"]


def test_updating_scope_rejects_client_used_by_another_scope(api):
    client, headers = api
    created_client = client.post(
        "/clients",
        headers=headers,
        json={
            "cnpj": "03.114.340/0001-31",
            "razao_social": "ORDEMILK LTDA.",
        },
    ).get_json()
    existing_scope = client.post(
        f"/scopes?clientId={created_client['id']}",
        headers=headers,
        json={},
    ).get_json()
    orphan_scope = client.post("/scopes", headers=headers, json={}).get_json()

    response = client.put(
        f"/scopes/{orphan_scope['id']}",
        headers=headers,
        json={
            "sobreEmpresa": {
                "cnpj": "03.114.340/0001-31",
                "razaoSocial": "ORDEMILK LTDA.",
            }
        },
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "client_scope_already_exists",
        "message": "Este cliente já possui um escopo.",
        "client_id": created_client["id"],
        "scope_id": existing_scope["id"],
    }
