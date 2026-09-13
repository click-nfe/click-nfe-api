import json
from datetime import datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Organization,
    Preposto,
    PrepostoContato,
    PrepostoCredenciado,
    PrepostoCredenciadoVinculo,
    PrepostoLocalidade,
    PrepostoTarifa,
    User,
)
from app.services.preposto_catalog_import import import_preposto_catalog_2025


class TestConfig:
    TESTING = True
    SECRET_KEY = "preposto-catalog-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False


def sample_catalog():
    return {
        "metadata": {
            "catalog": "TEST_CATALOG",
            "excluded_sections": [{"section": "W10", "reason": "Deferred"}],
        },
        "providers": [
            {"key": "P01", "source_sections": ["W01"], "name": "Quality Log", "existing_id": None, "existing_name": "Quality Log", "active": True, "notes": None},
            {"key": "P02", "source_sections": ["W02"], "name": "Maciel Despachos", "existing_id": None, "existing_name": None, "active": True, "notes": None},
            {"key": "P03", "source_sections": ["W03"], "name": "Tamayo Assessoria", "existing_id": None, "existing_name": "Tamayo", "active": True, "notes": None},
        ],
        "contacts": [
            {"key": "C01", "provider_key": "P01", "name": "André Junior", "email": "andre@example.invalid", "phone": "1", "whatsapp": None, "primary": True, "notes": None},
            {"key": "C02", "provider_key": "P01", "name": "Leonardo", "email": "leonardo@example.invalid", "phone": None, "whatsapp": None, "primary": False, "notes": None},
            {"key": "C03", "provider_key": "P02", "name": "Alexandre Maciel", "email": "maciel@example.invalid", "phone": "2", "whatsapp": None, "primary": True, "notes": None},
            {"key": "C04", "provider_key": "P03", "name": "Marco Aurelio", "email": "marco@example.invalid", "phone": "3", "whatsapp": None, "primary": True, "notes": None},
            {"key": "C05", "provider_key": "P03", "name": "Douglas Albuquerque", "email": "douglas@example.invalid", "phone": None, "whatsapp": None, "primary": False, "notes": None},
        ],
        "localities": [
            {"key": "L01", "section": "W01", "provider_key": "P01", "city": "Itajaí", "state": "SC", "description": "Porto de Itajaí", "location_type": "PORTO", "serves_import": True, "serves_export": True, "import_value": None, "export_value": 200, "import_value_description": None, "export_value_description": None, "currency": "BRL", "notes": None},
            {"key": "L02", "section": "W02", "provider_key": "P02", "city": "Itapoá", "state": "SC", "description": "Porto de Itapoá", "location_type": "PORTO", "serves_import": False, "serves_export": True, "import_value": None, "export_value": 50, "import_value_description": None, "export_value_description": None, "currency": "BRL", "notes": None},
            {"key": "L03", "section": "W03", "provider_key": "P03", "city": "Uruguaiana", "state": "RS", "description": "Fronteira Uruguaiana", "location_type": "FRONTEIRA", "serves_import": True, "serves_export": True, "import_value": 250, "export_value": 150, "import_value_description": None, "export_value_description": None, "currency": "BRL", "notes": None},
        ],
        "tariffs": [
            {"key": "T01", "provider_key": "P01", "locality_keys": ["L01"], "operation": "EXPORTACAO", "tariff_type": "BASE", "value": 200, "value_description": None, "condition": "Tarifa-base", "primary": True, "currency": "BRL", "active": True, "notes": None},
            {"key": "T02", "provider_key": "P01", "locality_keys": ["L01"], "operation": "IMPORTACAO", "tariff_type": "CLIENTE_ESPECIFICO", "value": 300, "value_description": None, "condition": "Cliente A", "primary": False, "currency": "BRL", "active": True, "notes": None},
            {"key": "T03", "provider_key": "P01", "locality_keys": ["L01"], "operation": "IMPORTACAO", "tariff_type": "CLIENTE_ESPECIFICO", "value": 750, "value_description": None, "condition": "Cliente B", "primary": False, "currency": "BRL", "active": True, "notes": None},
            {"key": "T04", "provider_key": "P02", "locality_keys": ["L02"], "operation": "EXPORTACAO", "tariff_type": "BASE", "value": 50, "value_description": None, "condition": "Tarifa-base", "primary": True, "currency": "BRL", "active": True, "notes": None},
            {"key": "T05", "provider_key": "P03", "locality_keys": ["L03"], "operation": "IMPORTACAO", "tariff_type": "BASE", "value": 250, "value_description": None, "condition": "Tarifa-base", "primary": True, "currency": "BRL", "active": True, "notes": None},
            {"key": "T06", "provider_key": "P03", "locality_keys": ["L03"], "operation": "EXPORTACAO", "tariff_type": "BASE", "value": 150, "value_description": None, "condition": "Tarifa-base", "primary": True, "currency": "BRL", "active": True, "notes": None},
        ],
        "credentials": [
            {"key": "D01", "provider_key": "P01", "name": "Credenciado Compartilhado", "cpf": "12345678901", "cpf_display": "123.456.789-01", "rfb_registration": "9D.01.001", "category": "DESPACHANTE", "active": True},
            {"key": "D02", "provider_key": "P03", "name": "Credenciado Compartilhado", "cpf": "12345678901", "cpf_display": "123.456.789-01", "rfb_registration": "9D.01.001", "category": "DESPACHANTE", "active": True},
        ],
        "bindings": [
            {"key": "V01", "credential_key": "D01", "provider_key": "P01", "locality_keys": ["L01"], "active": True, "notes": None},
            {"key": "V02", "credential_key": "D02", "provider_key": "P03", "locality_keys": ["L03"], "active": True, "notes": None},
        ],
    }


@pytest.fixture
def catalog_app(tmp_path):
    source_path = tmp_path / "approved-catalog.json"
    source_path.write_text(json.dumps(sample_catalog(), ensure_ascii=False), encoding="utf-8")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        organization = Organization(nome="Organização Teste", slug="prepostos-2025")
        db.session.add(organization)
        db.session.flush()

        quality = Preposto(id=uuid4(), organization_id=organization.id, nome="Quality Log", ativo=True)
        tamayo = Preposto(organization_id=organization.id, nome="Tamayo", ativo=True)
        db.session.add_all([quality, tamayo])
        db.session.flush()
        db.session.add(
            PrepostoContato(
                preposto_id=quality.id,
                nome="Vanessa",
                email="vanessa@example.invalid",
                principal=True,
            )
        )
        db.session.add(
            PrepostoLocalidade(
                preposto_id=quality.id,
                cidade="Itajaí",
                uf="SC",
                descricao_local="Porto de Itajaí",
                tipo_local="PORTO",
                atende_importacao=True,
                atende_exportacao=True,
                valor_importacao=150,
                valor_exportacao=150,
            )
        )
        db.session.commit()
        yield app, organization.id, source_path
        db.session.remove()
        db.drop_all()


def test_catalog_dry_run_does_not_persist_changes(catalog_app):
    _, organization_id, source_path = catalog_app
    result = import_preposto_catalog_2025(organization_id, source_path=source_path)

    assert result["applied"] is False
    assert Preposto.query.count() == 2
    assert PrepostoContato.query.one().nome == "Vanessa"
    assert PrepostoTarifa.query.count() == 0
    assert PrepostoCredenciado.query.count() == 0


def test_catalog_applies_approved_records_and_word_values(catalog_app):
    _, organization_id, source_path = catalog_app
    result = import_preposto_catalog_2025(
        organization_id,
        source_path=source_path,
        apply=True,
    )

    assert result["applied"] is True
    assert Preposto.query.filter_by(organization_id=organization_id).count() == 3
    quality = Preposto.query.filter_by(nome="Quality Log").one()
    assert {contact.nome for contact in quality.contatos} == {"André Junior", "Leonardo"}

    itajai = PrepostoLocalidade.query.filter_by(preposto_id=quality.id, cidade="Itajaí").one()
    assert itajai.valor_importacao is None
    assert float(itajai.valor_exportacao) == 200.0
    assert {tariff.condicao for tariff in itajai.tarifas} == {"Tarifa-base", "Cliente A", "Cliente B"}

    maciel = Preposto.query.filter_by(nome="Maciel Despachos").one()
    itapoa = PrepostoLocalidade.query.filter_by(preposto_id=maciel.id).one()
    assert itapoa.atende_importacao is False
    assert itapoa.valor_importacao is None
    assert float(itapoa.valor_exportacao) == 50.0

    tamayo = Preposto.query.filter_by(nome="Tamayo Assessoria").one()
    assert Preposto.query.filter_by(nome="Tamayo").first() is None
    assert {locality.cidade for locality in tamayo.localidades} == {"Uruguaiana"}
    assert PrepostoCredenciado.query.count() == 1
    assert PrepostoCredenciadoVinculo.query.count() == 2


def test_catalog_is_idempotent(catalog_app):
    _, organization_id, source_path = catalog_app
    import_preposto_catalog_2025(organization_id, source_path=source_path, apply=True)
    counts_before = (
        Preposto.query.count(),
        PrepostoContato.query.count(),
        PrepostoLocalidade.query.count(),
        PrepostoTarifa.query.count(),
        PrepostoCredenciado.query.count(),
        PrepostoCredenciadoVinculo.query.count(),
    )

    second = import_preposto_catalog_2025(organization_id, source_path=source_path, apply=True)
    counts_after = (
        Preposto.query.count(),
        PrepostoContato.query.count(),
        PrepostoLocalidade.query.count(),
        PrepostoTarifa.query.count(),
        PrepostoCredenciado.query.count(),
        PrepostoCredenciadoVinculo.query.count(),
    )
    assert counts_after == counts_before
    assert all(stats["created"] == 0 for stats in second["stats"].values())


def test_lookup_returns_selectable_tariffs_and_masked_credentials(catalog_app):
    app, organization_id, source_path = catalog_app
    import_preposto_catalog_2025(organization_id, source_path=source_path, apply=True)

    response = app.test_client().get(
        "/prepostos/public/lookup",
        query_string={"cidade": "Itajaí", "operacao": "IMPORTACAO"},
    )

    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["localidadeId"]
    assert {tariff["condicao"] for tariff in item["tarifas"]} == {"Cliente A", "Cliente B"}
    assert item["credenciados"][0]["cpfMascarado"] == "***.456.789-**"


def test_lookup_rejects_missing_operation(catalog_app):
    app, _, _ = catalog_app
    response = app.test_client().get("/prepostos/public/lookup")
    assert response.status_code == 422


def test_lookup_accepts_partial_city(catalog_app):
    app, organization_id, source_path = catalog_app
    import_preposto_catalog_2025(organization_id, source_path=source_path, apply=True)

    response = app.test_client().get(
        "/prepostos/public/lookup",
        query_string={"cidade": "taja", "operacao": "IMPORTACAO"},
    )

    assert response.status_code == 200
    assert response.get_json()["items"][0]["cidade"] == "Itajaí"


@pytest.fixture
def admin_catalog_api(catalog_app):
    app, organization_id, source_path = catalog_app
    import_preposto_catalog_2025(organization_id, source_path=source_path, apply=True)
    user = User(
        organization_id=organization_id,
        nome="Administrador de prepostos",
        email="prepostos-admin@example.invalid",
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
    return app.test_client(), {"Authorization": f"Bearer {token}"}


def test_admin_list_returns_all_records_and_searches_nested_fields(admin_catalog_api):
    client, headers = admin_catalog_api

    all_records = client.get("/prepostos", headers=headers)
    partial_city = client.get("/prepostos", headers=headers, query_string={"q": "tapo"})
    contact = client.get("/prepostos", headers=headers, query_string={"q": "alexandre"})

    assert all_records.status_code == 200
    assert all_records.get_json()["total"] == 3
    assert all_records.get_json()["summary"]["tarifas"] == 6
    assert partial_city.get_json()["items"][0]["nome"] == "Maciel Despachos"
    assert contact.get_json()["items"][0]["nome"] == "Maciel Despachos"


def test_admin_can_manage_tariffs_credentials_and_bindings(admin_catalog_api):
    client, headers = admin_catalog_api
    prepostos = client.get("/prepostos", headers=headers).get_json()["items"]
    maciel = next(item for item in prepostos if item["nome"] == "Maciel Despachos")
    localidade_id = maciel["localidades"][0]["id"]

    tariff = client.post(
        f"/prepostos/{maciel['id']}/localidades/{localidade_id}/tarifas",
        headers=headers,
        json={
            "codigo": "TESTE-CONDICIONAL",
            "operacao": "EXPORTACAO",
            "tipo": "CONDICIONAL",
            "valor": 375,
            "condicao": "Carga excedente",
        },
    )
    credential = client.post(
        "/prepostos/credenciados",
        headers=headers,
        json={
            "nome": "Despachante de teste",
            "cpf": "987.654.321-00",
            "registro_rfb": "RF-123",
        },
    )
    binding = client.post(
        f"/prepostos/{maciel['id']}/localidades/{localidade_id}/credenciados",
        headers=headers,
        json={"credenciado_id": credential.get_json()["id"]},
    )

    assert tariff.status_code == 201
    assert credential.status_code == 201
    assert credential.get_json()["cpf"] == "98765432100"
    assert binding.status_code == 201
    linked = binding.get_json()["credenciados"][0]
    assert linked["nome"] == "Despachante de teste"
    assert linked["localidade_ids"] == [localidade_id]
