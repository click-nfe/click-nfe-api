from datetime import datetime, timedelta

import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import Client, Organization, User


class TestConfig:
    TESTING = True
    SECRET_KEY = "tax-rule-resolution-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


def _access_token(app, user: User) -> str:
    now = datetime.utcnow()
    return jwt.encode(
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


@pytest.fixture
def tax_rule_api():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        organization_a = Organization(nome="Empresa Teste A", slug="teste-a")
        organization_b = Organization(nome="Empresa Teste B", slug="teste-b")
        db.session.add_all([organization_a, organization_b])
        db.session.flush()

        admin_a = User(
            organization_id=organization_a.id,
            nome="Administrador Teste A",
            email="admin-a@example.invalid",
            role="admin",
            ativo=True,
        )
        admin_a.set_password("test-password")
        client_a = Client(
            organization_id=organization_a.id,
            cnpj="00000000000191",
            razao_social="Cliente Teste A",
            ativo=True,
        )
        client_b = Client(
            organization_id=organization_b.id,
            cnpj="00000000000272",
            razao_social="Cliente Teste B",
            ativo=True,
        )
        db.session.add_all([admin_a, client_a, client_b])
        db.session.commit()

        yield {
            "http": app.test_client(),
            "headers": {
                "Authorization": f"Bearer {_access_token(app, admin_a)}"
            },
            "client_id": str(client_a.id),
            "other_client_id": str(client_b.id),
        }

        db.session.remove()
        db.drop_all()


def _rule_payload(**overrides):
    payload = {
        "name": "Regra sintética",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "import_modality": "direct",
        "tax_regime": "3",
        "priority": 100,
        "ncm_scope_type": "all",
        "ncm_patterns": [],
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": "90",
            "icms_rate": "12",
        },
    }
    payload.update(overrides)
    return payload


def _create_rule(api, **overrides):
    response = api["http"].post(
        f'/clients/{api["client_id"]}/import-tax-rules',
        headers=api["headers"],
        json=_rule_payload(**overrides),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_simulator_prefers_exact_then_prefix_then_default(tax_rule_api):
    default_rule = _create_rule(
        tax_rule_api,
        name="Regra geral sintética",
        priority=1000,
    )
    prefix_rule = _create_rule(
        tax_rule_api,
        name="Regra de capítulo sintética",
        ncm_scope_type="prefix",
        ncm_patterns=["84"],
        priority=100,
    )
    exact_rule = _create_rule(
        tax_rule_api,
        name="Regra exata sintética",
        ncm_scope_type="exact",
        ncm_patterns=["84212300", "84713012"],
        priority=1,
    )

    expected = (
        ("84212300", exact_rule["id"], "84212300"),
        ("84718000", prefix_rule["id"], "84"),
        ("87087090", default_rule["id"], ""),
    )
    for ncm, expected_rule_id, expected_pattern in expected:
        response = tax_rule_api["http"].post(
            f'/clients/{tax_rule_api["client_id"]}/import-tax-rules/simulate',
            headers=tax_rule_api["headers"],
            json={
                "issuer_state": "PR",
                "tax_regime": "3",
                "import_purpose": "resale",
                "import_modality": "direct",
                "ncm": ncm,
                "reference_date": "2026-09-19",
            },
        )

        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["status"] == "matched"
        assert body["selected_rule"]["id"] == expected_rule_id
        assert body["selection"]["matched_ncm_pattern"] == expected_pattern


def test_items_resolve_independently_and_detect_stale_snapshot(tax_rule_api):
    profile = tax_rule_api["http"].put(
        f'/clients/{tax_rule_api["client_id"]}/fiscal-profile',
        headers=tax_rule_api["headers"],
        json={
            "legal_name": "Cliente Fiscal Sintético",
            "cnpj": "00000000000191",
            "state_registration": "1234567890",
            "tax_regime": "3",
            "street": "Rua de Teste",
            "number": "100",
            "district": "Bairro de Teste",
            "city_code": "4106902",
            "city_name": "Cidade de Teste",
            "state": "PR",
            "zip_code": "80000000",
        },
    )
    assert profile.status_code == 200, profile.get_json()

    default_rule = _create_rule(
        tax_rule_api,
        name="Regra geral sintética",
        priority=1000,
    )
    exact_rule = _create_rule(
        tax_rule_api,
        name="Regra exata sintética",
        ncm_scope_type="exact",
        ncm_patterns=["84212300"],
        priority=1,
    )

    process = tax_rule_api["http"].post(
        "/import-processes",
        headers=tax_rule_api["headers"],
        json={"importer_id": tax_rule_api["client_id"], "source": "manual"},
    )
    assert process.status_code == 201, process.get_json()
    process_id = process.get_json()["id"]

    snapshot = tax_rule_api["http"].post(
        f"/import-processes/{process_id}/duimp-snapshots",
        headers=tax_rule_api["headers"],
        json={
            "duimp_number": "26BR0000999999-1",
            "raw_payload": {
                "numero": "26BR0000999999-1",
                "dataRegistro": "2026-09-19",
                "modalidadeImportacao": "direct",
                "itens": [
                    {
                        "numeroItem": "1",
                        "codigoProduto": "TESTE-001",
                        "descricao": "Item sintético um",
                        "ncm": "84212300",
                        "quantidade": "1",
                        "unidade": "UN",
                        "valorProduto": "100.00",
                    },
                    {
                        "numeroItem": "2",
                        "codigoProduto": "TESTE-002",
                        "descricao": "Item sintético dois",
                        "ncm": "87087090",
                        "quantidade": "1",
                        "unidade": "UN",
                        "valorProduto": "100.00",
                    },
                ],
            },
        },
    )
    assert snapshot.status_code == 201, snapshot.get_json()
    snapshot_id = snapshot.get_json()["id"]

    classified = tax_rule_api["http"].put(
        f"/import-processes/{process_id}/item-classifications",
        headers=tax_rule_api["headers"],
        json={
            "duimp_snapshot_id": snapshot_id,
            "items": [
                {"duimp_item_number": "1", "import_purpose": "resale"},
                {"duimp_item_number": "2", "import_purpose": "resale"},
            ],
        },
    )
    assert classified.status_code == 200, classified.get_json()
    items = {
        item["duimp_item_number"]: item
        for item in classified.get_json()["items"]
    }
    assert items["1"]["tax_rule"]["id"] == exact_rule["id"]
    assert items["2"]["tax_rule"]["id"] == default_rule["id"]
    assert items["1"]["tax_rule"]["applied_revision"] == 1

    updated = tax_rule_api["http"].put(
        (
            f'/clients/{tax_rule_api["client_id"]}/import-tax-rules/'
            f'{exact_rule["id"]}'
        ),
        headers=tax_rule_api["headers"],
        json={
            "configuration_json": {
                "cfop": "3102",
                "icms_origin": "1",
                "icms_cst": "90",
                "icms_rate": "18",
            }
        },
    )
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["revision"] == 2

    state = tax_rule_api["http"].get(
        f"/import-processes/{process_id}/item-classifications",
        headers=tax_rule_api["headers"],
        query_string={"duimp_snapshot_id": snapshot_id},
    )
    assert state.status_code == 200, state.get_json()
    items = {
        item["duimp_item_number"]: item
        for item in state.get_json()["items"]
    }
    assert items["1"]["status"] == "stale_tax_rule"
    assert items["1"]["tax_rule"]["revision"] == 2
    assert items["1"]["tax_rule"]["applied_revision"] == 1
    assert items["2"]["status"] == "classified"


def test_simulator_cannot_access_another_organization(tax_rule_api):
    response = tax_rule_api["http"].post(
        (
            f'/clients/{tax_rule_api["other_client_id"]}'
            "/import-tax-rules/simulate"
        ),
        headers=tax_rule_api["headers"],
        json={
            "issuer_state": "PR",
            "tax_regime": "3",
            "import_purpose": "resale",
            "import_modality": "direct",
            "ncm": "84212300",
            "reference_date": "2026-09-19",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "bad_request",
        "message": "Cliente não encontrado para a organização atual.",
    }
