from datetime import datetime, timedelta
from uuid import UUID

import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import Client, NfeDraft, Organization, User
from app.services.import_process import ImportNfeService
from app.models.nfe_issuance import (
    NfeAttemptStatus,
    NfeIssuance,
    NfeIssuanceAttempt,
    NfeIssuanceEvent,
)
from tests.helpers import StaticCertificateVault, certificate_material


class TestConfig:
    TESTING = True
    SECRET_KEY = "integration-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


@pytest.fixture
def api():
    app = create_app(TestConfig)
    app.config["NFE_CERTIFICATE_VAULT"] = StaticCertificateVault(
        certificate_material("00000000000191")
    )
    with app.app_context():
        db.create_all()
        organization = Organization(nome="Organização Teste", slug="org-teste")
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            nome="Operador Teste",
            email="operador@example.invalid",
            role="admin",
            ativo=True,
        )
        user.set_password("test-password")
        client = Client(
            organization_id=organization.id,
            cnpj="00000000000191",
            razao_social="Importadora Teste Ltda",
            ativo=True,
        )
        db.session.add_all([user, client])
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
        yield app.test_client(), {"Authorization": f"Bearer {token}"}, str(client.id)
        db.session.remove()
        db.drop_all()


def test_api_flow_from_manual_duimp_snapshot_to_unsigned_xml(api):
    client, headers, importer_id = api

    profile = client.put(
        f"/clients/{importer_id}/fiscal-profile",
        headers=headers,
        json={
            "legal_name": "Importadora Teste Ltda",
            "cnpj": "00000000000191",
            "state_registration": "1234567890",
            "tax_regime": "3",
            "street": "Rua de Teste",
            "number": "100",
            "district": "Centro",
            "city_code": "4106902",
            "city_name": "Curitiba",
            "state": "PR",
            "zip_code": "80000000",
            "country_code": "1058",
            "country_name": "Brasil",
        },
    )
    assert profile.status_code == 200

    process_response = client.post(
        "/import-processes",
        headers=headers,
        json={
            "importer_id": importer_id,
            "reference_code": "TESTE-DUIMP-001",
            "duimp_number": "26BR0000000000-1",
            "source": "manual",
        },
    )
    assert process_response.status_code == 201
    process_id = process_response.get_json()["id"]

    draft_response = client.post(
        f"/import-processes/{process_id}/nfe-draft/from-duimp",
        headers=headers,
        json={
            "environment": "production",
            "series": "1",
            "number": 14422,
            "import_purpose": "resale",
            "duimp_payload": {
                "numero": "26BR0000000000-1",
                "versao": "1",
                "dataRegistro": "2026-07-14",
                "localDesembaraco": "0917800 - PORTO DE PARANAGUA",
                "ufDesembaraco": "PR",
                "dataDesembaraco": "2026-07-15",
                "viaTransporteCodigo": "1",
                "tipoIntermedio": "1",
                "modalidadeImportacao": "direct",
                "itens": [
                    {
                        "numeroItem": "1",
                        "codigoProduto": "PROD-001",
                        "descricao": "Produto sanitizado para teste",
                        "ncm": "87087090",
                        "quantidade": "12",
                        "unidade": "PECAS",
                        "valorProduto": "6054.39",
                        "valorUnitario": "504.5325",
                        "sequenciaAdicao": "1",
                        "codigoFabricante": "0000",
                        "codigoExportador": "EXP-TESTE-001",
                        "tributos": {
                            "ii": {"base": "6054.39", "rate": "18", "value": "1089.79"},
                            "ipi": {"base": "7144.18", "rate": "3.25", "value": "232.19"},
                            "pis": {"base": "6054.39", "rate": "3.12", "value": "188.90"},
                            "cofins": {"base": "6054.39", "rate": "14.37", "value": "870.02"},
                        },
                    }
                ],
            },
            "foreign_supplier": {
                "name": "Foreign Supplier Ltd",
                "foreign_id": "",
                "country_code": "1600",
                "country_name": "CHINA",
                "address": {
                    "street": "EXTERIOR",
                    "number": "0",
                    "district": "EXTERIOR",
                    "city_name": "EXTERIOR",
                },
            },
            "tax_configuration": {
                "icms_rate": "12",
                "icms_origin": "1",
                "icms_cst": "90",
                "ipi_cst": "49",
                "pis_cst": "98",
                "cofins_cst": "98",
            },
            "additional_costs": {"afrmm": "53.46", "other": "35.36"},
        },
    )
    assert draft_response.status_code == 201, draft_response.get_json()
    draft_body = draft_response.get_json()
    assert draft_body["validation"]["valid"] is True
    draft_id = draft_body["draft"]["id"]

    key_response = client.post(
        f"/nfe-drafts/{draft_id}/generate-access-key",
        headers=headers,
        json={},
    )
    assert key_response.status_code == 200, key_response.get_json()
    assert len(key_response.get_json()["access_key"]) == 44

    xml_response = client.post(
        f"/nfe-drafts/{draft_id}/generate-xml",
        headers=headers,
        json={},
    )
    assert xml_response.status_code == 201, xml_response.get_json()
    xml_body = xml_response.get_json()
    xml_version_id = xml_body["id"]
    xml = xml_body["xml_content"]
    assert "<nDI>26BR00000000001</nDI>" in xml
    assert "<UF>EX</UF>" in xml
    assert "<Signature" not in xml
    assert ".000000-03:00" not in xml

    download_response = client.get(
        f"/nfe-drafts/{draft_id}/xml-versions/{xml_version_id}/download",
        headers=headers,
    )
    assert download_response.status_code == 200
    assert download_response.content_type == "application/xml; charset=utf-8"
    assert download_response.data.startswith(b"<?xml")
    assert b'"xml_content"' not in download_response.data
    assert (
        f"NFe-{xml_body['access_key']}-unsigned-v1.xml"
        in download_response.headers["Content-Disposition"]
    )

    xsd_response = client.post(
        f"/nfe-drafts/{draft_id}/xml-versions/{xml_version_id}/validate-xsd",
        headers=headers,
        json={},
    )
    assert xsd_response.status_code == 200, xsd_response.get_json()
    xsd_body = xsd_response.get_json()
    assert xsd_body["xsd_valid"] is True
    assert xsd_body["xsd_errors"] == []
    assert xsd_body["schema"] == {
        "package": "PL_010e_v1.02",
        "file": "nfe_v4.00.xsd",
    }

    versions_response = client.get(
        f"/nfe-drafts/{draft_id}/xml-versions",
        headers=headers,
    )
    assert versions_response.status_code == 200
    assert versions_response.get_json()[0]["xsd_valid"] is True
    assert versions_response.get_json()[0]["xsd_errors"] == []

    history_response = client.get(
        f"/import-processes/{process_id}/nfe-drafts",
        headers=headers,
    )
    assert history_response.status_code == 200
    history = history_response.get_json()["items"]
    assert len(history) == 1
    assert history[0]["id"] == draft_id
    assert history[0]["items_count"] == 1
    assert history[0]["access_key"] == xml_body["access_key"]
    assert history[0]["xml_versions"][0]["id"] == xml_version_id
    assert history[0]["xml_versions"][0]["xsd_valid"] is True
    assert "xml_content" not in history[0]["xml_versions"][0]
    assert "fiscal_payload" not in history[0]

    original_xml_version_id = xml_version_id
    edit_after_xml = client.patch(
        f"/nfe-drafts/{draft_id}",
        headers=headers,
        json={
            "issuer": {"state_registration": "123.456.789-0"},
            "foreign_supplier": {"foreign_id": None},
        },
    )
    assert edit_after_xml.status_code == 200, edit_after_xml.get_json()
    assert edit_after_xml.get_json()["requires_new_xml"] is True
    assert (
        edit_after_xml.get_json()["draft"]["fiscal_payload"]["issuer"][
            "state_registration"
        ]
        == "1234567890"
    )
    assert (
        edit_after_xml.get_json()["draft"]["fiscal_payload"]["recipient"][
            "foreign_id"
        ]
        is None
    )

    workflow_after_edit = client.get(
        f"/import-processes/{process_id}/nfe-workflow-state",
        headers=headers,
        query_string={
            "environment": "production",
            "series": "1",
        },
    )
    assert workflow_after_edit.status_code == 200
    # A ausência de regra persistida não volta a bloquear o processo. Este
    # cenário legado ainda possui dados de contexto não normalizados e, por
    # isso, a próxima ação válida passa a ser a complementação do contexto.
    assert workflow_after_edit.get_json()["next_action"] == "resolve_context"

    replacement_xml_response = client.post(
        f"/nfe-drafts/{draft_id}/generate-xml",
        headers=headers,
        json={},
    )
    assert replacement_xml_response.status_code == 201
    xml_body = replacement_xml_response.get_json()
    xml_version_id = xml_body["id"]
    assert xml_body["version_number"] == 2

    replacement_xsd_response = client.post(
        (
            f"/nfe-drafts/{draft_id}/xml-versions/"
            f"{xml_version_id}/validate-xsd"
        ),
        headers=headers,
        json={},
    )
    assert replacement_xsd_response.status_code == 200
    assert replacement_xsd_response.get_json()["xsd_valid"] is True

    certificate_response = client.post(
        f"/clients/{importer_id}/fiscal-certificates",
        headers=headers,
        json={
            "environment": "production",
            "provider": "gcp_secret_manager",
            "certificate_ref": "gcp:nfe-hom-client-test-pfx@1",
            "password_ref": "gcp:nfe-hom-client-test-password@1",
        },
    )
    assert certificate_response.status_code == 201
    certificate_body = certificate_response.get_json()
    certificate_id = certificate_body["id"]
    assert certificate_body["status"] == "pending_validation"
    assert certificate_body["is_active"] is False
    assert "certificate_ref" not in certificate_body
    assert "password_ref" not in certificate_body

    validation_response = client.post(
        (
            f"/clients/{importer_id}/fiscal-certificates/"
            f"{certificate_id}/validate"
        ),
        headers=headers,
        json={},
    )
    assert validation_response.status_code == 200
    assert validation_response.get_json()["valid"] is True
    assert (
        validation_response.get_json()["issuer_cnpj"]
        == "00000000000191"
    )
    assert len(
        validation_response.get_json()["certificate_fingerprint_sha256"]
    ) == 64

    activation_response = client.post(
        (
            f"/clients/{importer_id}/fiscal-certificates/"
            f"{certificate_id}/activate"
        ),
        headers=headers,
        json={},
    )
    assert activation_response.status_code == 200
    assert activation_response.get_json()["status"] == "active"
    assert activation_response.get_json()["is_active"] is True

    stale_signature_response = client.post(
        (
            f"/nfe-drafts/{draft_id}/xml-versions/"
            f"{original_xml_version_id}/sign"
        ),
        headers=headers,
        json={"certificate_id": certificate_id},
    )
    assert stale_signature_response.status_code == 400
    assert "mais recente" in stale_signature_response.get_json()["message"]

    signature_response = client.post(
        f"/nfe-drafts/{draft_id}/xml-versions/{xml_version_id}/sign",
        headers=headers,
        json={"certificate_id": certificate_id},
    )
    assert signature_response.status_code == 201, (
        signature_response.get_json()
    )
    signature_body = signature_response.get_json()
    signed_version = signature_body["xml_version"]
    signed_version_id = signed_version["id"]
    assert signature_body["replayed"] is False
    assert signature_body["issuance"]["status"] == "signed"
    assert signed_version["xml_type"] == "SIGNED"
    assert signed_version["version_number"] == 2
    assert signed_version["xsd_valid"] is True
    assert signed_version["xsd_errors"] == []
    assert "<Signature" in signed_version["xml_content"]
    assert "<X509Certificate>" in signed_version["xml_content"]

    replay_response = client.post(
        f"/nfe-drafts/{draft_id}/xml-versions/{xml_version_id}/sign",
        headers=headers,
        json={"certificate_id": certificate_id},
    )
    assert replay_response.status_code == 200
    assert replay_response.get_json()["replayed"] is True
    assert (
        replay_response.get_json()["xml_version"]["id"]
        == signed_version_id
    )

    signed_download = client.get(
        (
            f"/nfe-drafts/{draft_id}/xml-versions/"
            f"{signed_version_id}/download"
        ),
        headers=headers,
    )
    assert signed_download.status_code == 200
    assert b"<Signature" in signed_download.data
    assert (
        f"NFe-{xml_body['access_key']}-signed-v2.xml"
        in signed_download.headers["Content-Disposition"]
    )

    process_after_signature = client.get(
        f"/import-processes/{process_id}",
        headers=headers,
    )
    assert process_after_signature.status_code == 200
    assert process_after_signature.get_json()["status"] == "XML_SIGNED"

    with client.application.app_context():
        issuance = NfeIssuance.query.filter_by(
            nfe_draft_id=UUID(draft_id)
        ).one()
        assert issuance.status == "signed"
        assert str(issuance.certificate_id) == certificate_id

        attempts = NfeIssuanceAttempt.query.filter_by(
            nfe_issuance_id=issuance.id
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == NfeAttemptStatus.SUCCEEDED
        assert len(attempts[0].request_checksum) == 64
        assert len(attempts[0].response_checksum) == 64

        events = NfeIssuanceEvent.query.filter_by(
            nfe_issuance_id=issuance.id
        ).all()
        assert len(events) == 1
        assert events[0].previous_status == "xsd_validated"
        assert events[0].current_status == "signed"
        assert "certificate_ref" not in events[0].event_metadata
        assert "password_ref" not in events[0].event_metadata


def test_tax_rule_conflict_is_rejected_and_diagnosed(api):
    client, headers, importer_id = api
    payload = {
        "name": "PR revenda padrão A",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "import_modality": "direct",
        "tax_regime": "3",
        "priority": 100,
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_rate": "12",
            "icms_cst": "90",
        },
    }

    first = client.post(
        f"/clients/{importer_id}/import-tax-rules",
        headers=headers,
        json=payload,
    )
    assert first.status_code == 201, first.get_json()

    conflicting = client.post(
        f"/clients/{importer_id}/import-tax-rules",
        headers=headers,
        json={**payload, "name": "PR revenda padrão B"},
    )
    assert conflicting.status_code == 409
    conflict_body = conflicting.get_json()
    assert conflict_body["error"] == "tax_rule_conflict"
    assert conflict_body["conflicts"] == [
        {
            "id": first.get_json()["id"],
            "name": "PR revenda padrão A",
            "issuer_state": "PR",
            "import_purpose": "resale",
            "import_modality": "direct",
            "tax_regime": "3",
            "ncm_pattern": None,
            "priority": 100,
            "effective_from": None,
            "effective_until": None,
        }
    ]

    diagnostics = client.get(
        f"/clients/{importer_id}/import-tax-rules/diagnostics",
        headers=headers,
    )
    assert diagnostics.status_code == 200
    assert diagnostics.get_json()["summary"] == {
        "total": 1,
        "active": 1,
        "inactive": 0,
        "conflict_count": 0,
    }


def test_missing_tax_rule_does_not_block_duimp_preparation(api):
    client, headers, importer_id = api
    profile = client.put(
        f"/clients/{importer_id}/fiscal-profile",
        headers=headers,
        json={
            "legal_name": "Importadora Teste Ltda",
            "cnpj": "00000000000191",
            "state_registration": "1234567890",
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
    process = client.post(
        "/import-processes",
        headers=headers,
        json={"importer_id": importer_id, "source": "portal_unico"},
    )
    assert process.status_code == 201

    workflow = client.get(
        f"/import-processes/{process.get_json()['id']}/nfe-workflow-state",
        headers=headers,
    )
    assert workflow.status_code == 200
    body = workflow.get_json()
    assert body["prerequisites"]["has_active_tax_rule"] is False
    assert body["next_action"] == "configure_number_sequence"


def test_api_uses_client_tax_rule_and_persisted_nfe_context(api):
    client, headers, importer_id = api

    profile_response = client.put(
        f"/clients/{importer_id}/fiscal-profile",
        headers=headers,
        json={
            "legal_name": "Importadora Teste Ltda",
            "cnpj": "00000000000191",
            "state_registration": "1234567890",
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
    assert profile_response.status_code == 200

    rule_response = client.post(
        f"/clients/{importer_id}/import-tax-rules",
        headers=headers,
        json={
            "name": "PR revenda direta padrão",
            "issuer_state": "PR",
            "import_purpose": "resale",
            "import_modality": "direct",
            "tax_regime": "3",
            "priority": 100,
            "configuration_json": {
                "cfop": "3102",
                "icms_rate": "12",
                "icms_origin": "1",
                "icms_cst": "90",
                "ipi_cst": "49",
                "pis_cst": "98",
                "cofins_cst": "98",
                "document_defaults": {
                    "operation_nature": "Compra para comercialização",
                    "presence_indicator": "9",
                    "intermediary_indicator": "0",
                },
                "item_defaults": {
                    "commercial_unit": "PCE",
                    "taxable_unit": "UN",
                },
                "additional_info_defaults": {
                    "automatic_summary": True,
                    "legal_text": "Benefício fiscal de teste.",
                },
            },
            "transport_defaults": {
                "freight_mode": "1",
                "carrier": {
                    "tax_id": "06255344000128",
                    "name": "Transportadora Teste",
                    "state_registration": "9086113225",
                    "address": "Rua da Transportadora, 800",
                    "city_name": "Curitiba",
                    "state": "PR",
                },
                "volume": {
                    "quantity": 1,
                    "species": "CAIXA",
                    "gross_weight": "3.000",
                },
            },
            "payment_defaults": {"method": "90", "value": "0.00"},
        },
    )
    assert rule_response.status_code == 201, rule_response.get_json()
    rule_id = rule_response.get_json()["id"]

    consumption_rule_response = client.post(
        f"/clients/{importer_id}/import-tax-rules",
        headers=headers,
        json={
            "name": "PR uso e consumo direta",
            "issuer_state": "PR",
            "import_purpose": "use_consumption",
            "import_modality": "direct",
            "tax_regime": "3",
            "priority": 100,
            "effective_from": "2026-08-18",
            "configuration_json": {
                "cfop": "3556",
                "icms_rate": "12",
                "icms_origin": "1",
                "icms_cst": "90",
                "ipi_cst": "49",
                "pis_cst": "98",
                "cofins_cst": "98",
                "document_defaults": {
                    "operation_nature": "Importação para uso ou consumo",
                },
            },
        },
    )
    assert consumption_rule_response.status_code == 201
    consumption_rule_id = consumption_rule_response.get_json()["id"]

    process_response = client.post(
        "/import-processes",
        headers=headers,
        json={
            "importer_id": importer_id,
            "reference_code": "TESTE-AUTOMACAO-001",
            "duimp_number": "26BR0000000000-1",
            "source": "manual",
        },
    )
    assert process_response.status_code == 201
    process_id = process_response.get_json()["id"]

    snapshot_response = client.post(
        f"/import-processes/{process_id}/duimp-snapshots",
        headers=headers,
        json={
            "duimp_number": "26BR0000000000-1",
            "duimp_version": "1",
            "raw_payload": {
                "numero": "26BR0000000000-1",
                "versao": "1",
                "dataRegistro": "2026-07-14",
                "modalidadeImportacao": "direct",
                "itens": [
                    {
                        "numeroItem": "1",
                        "codigoProduto": "PROD-001",
                        "descricao": "Produto automatizado para revenda",
                        "ncm": "87087090",
                        "quantidade": "2",
                        "unidade": "UN",
                        "pesoLiquido": "2.500",
                        "valorProduto": "100.00",
                        "valorUnitario": "50.00",
                        "tributos": {
                            "ii": {"base": "100", "rate": "18", "value": "18"},
                            "ipi": {"base": "118", "rate": "3.25", "value": "3.84"},
                            "pis": {"base": "100", "rate": "3.12", "value": "3.12"},
                            "cofins": {"base": "100", "rate": "14.37", "value": "14.37"},
                        },
                    },
                    {
                        "numeroItem": "2",
                        "codigoProduto": "PROD-002",
                        "descricao": "Produto automatizado para uso e consumo",
                        "ncm": "84212300",
                        "quantidade": "1",
                        "unidade": "UN",
                        "pesoLiquido": "1.000",
                        "valorProduto": "50.00",
                        "valorUnitario": "50.00",
                        "tributos": {
                            "ii": {"base": "50", "rate": "16", "value": "8"},
                            "ipi": {"base": "58", "rate": "3.25", "value": "1.89"},
                            "pis": {"base": "50", "rate": "3.12", "value": "1.56"},
                            "cofins": {"base": "50", "rate": "14.37", "value": "7.19"},
                        },
                    },
                ],
            },
        },
    )
    assert snapshot_response.status_code == 201, snapshot_response.get_json()
    snapshot_id = snapshot_response.get_json()["id"]

    before = client.get(
        f"/import-processes/{process_id}/nfe-context",
        headers=headers,
        query_string={
            "duimp_snapshot_id": snapshot_id,
            "import_purpose": "resale",
        },
    )
    assert before.status_code == 200, before.get_json()
    assert before.get_json()["ready_for_draft"] is False
    assert "clearance_date" in before.get_json()["missing_fields"]
    assert before.get_json()["tax_rule"]["id"] == rule_id

    resolved = client.post(
        f"/import-processes/{process_id}/nfe-context/resolve",
        headers=headers,
        json={
            "duimp_snapshot_id": snapshot_id,
            "import_purpose": "resale",
            "refresh_external": False,
            "overrides": {
                "clearance_location": "PORTO DE PARANAGUA",
                "clearance_state": "PR",
                "clearance_date": "2026-07-15",
                "transport_mode_code": "1",
                "foreign_supplier": {
                    "name": "Foreign Supplier Ltd",
                    "country_code": "1600",
                    "country_name": "CHINA",
                },
            },
        },
    )
    assert resolved.status_code == 200, resolved.get_json()
    assert resolved.get_json()["ready_for_draft"] is True
    assert (
        resolved.get_json()["fields"]["clearance_date"]["source"]
        == "operator_override"
    )

    draft_response = client.post(
        f"/import-processes/{process_id}/nfe-draft/from-duimp",
        headers=headers,
        json={
            "environment": "production",
            "series": "1",
            "import_purpose": "resale",
            "duimp_snapshot_id": snapshot_id,
            "document": {},
            "item_defaults": {},
            "transport": {},
            "payment": {},
            "additional_info": {},
        },
    )
    assert draft_response.status_code == 201, draft_response.get_json()
    body = draft_response.get_json()
    assert body["validation"]["valid"] is True
    assert body["tax_rule"]["id"] == rule_id
    fiscal_payload = body["draft"]["fiscal_payload"]
    assert fiscal_payload["document"]["operation_nature"] == (
        "Compra para comercialização"
    )
    assert fiscal_payload["document"]["presence_indicator"] == "9"
    assert fiscal_payload["document"]["intermediary_indicator"] == "0"
    assert fiscal_payload["items"][0]["commercial_unit"] == "PCE"
    assert fiscal_payload["items"][0]["taxable_unit"] == "UN"
    assert fiscal_payload["transport"]["carrier"]["name"] == (
        "Transportadora Teste"
    )
    assert fiscal_payload["transport"]["volume"]["net_weight"] == "3.500"
    assert "II: R$ 26,00" in fiscal_payload["additional_info"][
        "complementary"
    ]

    process_list = client.get(
        "/import-processes",
        headers=headers,
        query_string={"created_by_me": "true"},
    )
    assert process_list.status_code == 200, process_list.get_json()
    listed = process_list.get_json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == process_id
    assert listed["items"][0]["created_by_me"] is True

    workflow = client.get(
        f"/import-processes/{process_id}/nfe-workflow-state",
        headers=headers,
        query_string={
            "import_purpose": "resale",
            "environment": "production",
            "series": "1",
        },
    )
    assert workflow.status_code == 200, workflow.get_json()
    workflow_body = workflow.get_json()
    assert workflow_body["process"]["id"] == process_id
    assert workflow_body["latest_snapshot"]["id"] == snapshot_id
    assert workflow_body["latest_draft"]["draft"]["id"] == body["draft"]["id"]
    assert workflow_body["prerequisites"] == {
        "has_fiscal_profile": True,
        "has_active_tax_rule": True,
        "active_tax_rule_count": 2,
        "tax_rule_conflict_count": 0,
        "has_number_sequence": False,
        "has_provider_connection": False,
        "has_item_classification": False,
        "item_classification_ready": False,
        "has_document_plan": False,
        "planned_documents_count": 0,
        "import_purpose": "resale",
        "environment": "production",
        "series": "1",
    }
    assert workflow_body["next_action"] == "configure_number_sequence"
    assert "Benefício fiscal de teste." in fiscal_payload[
        "additional_info"
    ]["complementary"]
    assert body["draft"]["fiscal_payload"]["source"] == {
        "duimp_source": "DUIMP",
        "fiscal_profile_id": profile_response.get_json()["id"],
        "import_process_id": process_id,
        "tax_configuration_source": "client_import_tax_rule",
        "tax_rule_id": rule_id,
        "tax_rule_ids": [rule_id],
    }

    classification_before = client.get(
        f"/import-processes/{process_id}/item-classifications",
        headers=headers,
        query_string={"duimp_snapshot_id": snapshot_id},
    )
    assert classification_before.status_code == 200
    assert classification_before.get_json()["pending_count"] == 2
    assert all(
        item["status"] == "unclassified"
        for item in classification_before.get_json()["items"]
    )

    classification_saved = client.put(
        f"/import-processes/{process_id}/item-classifications",
        headers=headers,
        json={
            "duimp_snapshot_id": snapshot_id,
            "items": [
                {
                    "duimp_item_number": "1",
                    "import_purpose": "resale",
                },
                {
                    "duimp_item_number": "2",
                    "import_purpose": "use_consumption",
                },
            ],
        },
    )
    assert classification_saved.status_code == 200, classification_saved.get_json()
    classification_body = classification_saved.get_json()
    assert classification_body["ready_for_draft"] is False
    assert classification_body["registration_date"] == "2026-07-14"
    classified_items = {
        item["duimp_item_number"]: item
        for item in classification_body["items"]
    }
    assert classified_items["1"]["cfop"] == "3102"
    assert classified_items["1"]["tax_rule"]["id"] == rule_id
    assert classified_items["2"]["status"] == "missing_tax_rule"
    candidate = classified_items["2"]["rule_candidates"][0]
    assert candidate["id"] == consumption_rule_id
    assert candidate["mismatch_reasons"] == ["effective_from"]
    assert candidate["effective_from"] == "2026-08-18"

    rule_update = client.put(
        (
            f"/clients/{importer_id}/import-tax-rules/"
            f"{consumption_rule_id}"
        ),
        headers=headers,
        json={"effective_from": "2026-07-14"},
    )
    assert rule_update.status_code == 200, rule_update.get_json()

    classification_reapplied = client.put(
        f"/import-processes/{process_id}/item-classifications",
        headers=headers,
        json={
            "duimp_snapshot_id": snapshot_id,
            "items": [
                {
                    "duimp_item_number": "1",
                    "import_purpose": "resale",
                },
                {
                    "duimp_item_number": "2",
                    "import_purpose": "use_consumption",
                },
            ],
        },
    )
    assert classification_reapplied.status_code == 200
    classification_body = classification_reapplied.get_json()
    assert classification_body["ready_for_draft"] is True
    classified_items = {
        item["duimp_item_number"]: item
        for item in classification_body["items"]
    }
    assert classified_items["2"]["cfop"] == "3556"
    assert (
        classified_items["2"]["tax_rule"]["id"]
        == consumption_rule_id
    )

    mixed_draft_response = client.post(
        f"/import-processes/{process_id}/nfe-draft/from-duimp",
        headers=headers,
        json={
            "environment": "production",
            "series": "1",
            "import_purpose": "resale",
            "duimp_snapshot_id": snapshot_id,
        },
    )
    assert mixed_draft_response.status_code == 201, mixed_draft_response.get_json()
    mixed_payload = mixed_draft_response.get_json()["draft"]["fiscal_payload"]
    assert mixed_payload["document"]["mixed_import_purposes"] is True
    assert (
        mixed_payload["document"]["operation_nature"]
        == "Importação de mercadorias"
    )
    mixed_items = {
        item["duimp_item_number"]: item
        for item in mixed_payload["items"]
    }
    assert mixed_items["1"]["import_purpose"] == "resale"
    assert mixed_items["1"]["cfop"] == "3102"
    assert mixed_items["2"]["import_purpose"] == "use_consumption"
    assert mixed_items["2"]["cfop"] == "3556"

    draft_id = body["draft"]["id"]
    update_response = client.patch(
        f"/nfe-drafts/{draft_id}",
        headers=headers,
        json={
            "document": {
                "operation_nature": "Importação para revenda"
            },
            "issuer": {
                "state_registration": "123.456.789-0",
            },
            "foreign_supplier": {
                "legal_name": "Foreign Supplier Corrected Ltd",
                "foreign_id": None,
                "country_code": "1600",
                "country_name": "CHINA",
                "address": {
                    "city_name": "SHANGHAI",
                },
            },
            "item_defaults": {
                "commercial_unit": "CX",
                "taxable_unit": "KG",
            },
            "transport": {
                "volume": {
                    "quantity": 2,
                    "net_weight": "4.000",
                    "gross_weight": "4.500",
                }
            },
            "payment": {"method": "90", "value": "0.00"},
            "additional_info": {
                "automatic_summary": True,
                "legal_text": "Complemento informado pelo operador."
            },
        },
    )
    assert update_response.status_code == 200, update_response.get_json()
    updated = update_response.get_json()
    assert updated["validation"]["valid"] is True
    assert updated["requires_new_xml"] is False
    updated_document = updated["draft"]["fiscal_payload"]["document"]
    assert updated_document["operation_nature"] == "Importação para revenda"
    assert updated_document["presence_indicator"] == "9"
    assert updated_document["intermediary_indicator"] == "0"
    assert updated_document["environment"] == "production"
    assert updated_document["series"] == "1"
    updated_issuer = updated["draft"]["fiscal_payload"]["issuer"]
    assert updated_issuer["state_registration"] == "1234567890"
    updated_recipient = updated["draft"]["fiscal_payload"]["recipient"]
    assert updated_recipient["foreign_id"] is None
    assert updated_recipient["legal_name"] == (
        "Foreign Supplier Corrected Ltd"
    )
    assert updated_recipient["address"]["country_code"] == "1600"
    assert updated_recipient["address"]["country_name"] == "CHINA"
    assert updated_recipient["address"]["city_name"] == "SHANGHAI"
    updated_transport = updated["draft"]["fiscal_payload"]["transport"]
    assert updated_transport["carrier"]["name"] == "Transportadora Teste"
    assert updated_transport["volume"] == {
        "gross_weight": "4.500",
        "net_weight": "4.000",
        "net_weight_source": "operator_override",
        "quantity": 2,
        "species": "CAIXA",
    }
    assert updated["draft"]["fiscal_payload"]["payment"]["value"] == "0.00"
    assert updated["items"][0]["commercial_unit"] == "CX"
    assert updated["items"][0]["taxable_unit"] == "KG"
    updated_complementary = updated["draft"]["fiscal_payload"][
        "additional_info"
    ]["complementary"]
    assert "Benefício fiscal de teste." not in updated_complementary
    assert updated_complementary.count("Conforme DUIMP:") == 1
    assert "Complemento informado pelo operador." in updated["draft"][
        "fiscal_payload"
    ]["additional_info"]["complementary"]

    # Um processo totalmente classificado e ainda sem rascunho deve montar
    # primeiro o plano documental auditável.
    for existing_draft in NfeDraft.query.filter_by(
        import_process_id=UUID(process_id)
    ).all():
        db.session.delete(existing_draft)
    db.session.commit()

    classified_workflow = client.get(
        f"/import-processes/{process_id}/nfe-workflow-state",
        headers=headers,
        query_string={
            "import_purpose": "resale",
            "environment": "production",
            "series": "1",
        },
    )
    assert classified_workflow.status_code == 200
    classified_workflow_body = classified_workflow.get_json()
    assert (
        classified_workflow_body["prerequisites"][
            "item_classification_ready"
        ]
        is True
    )
    assert classified_workflow_body["latest_draft"] is None
    assert classified_workflow_body["next_action"] == "create_document_plan"
    assert classified_workflow_body["current_step"] == "planning"
    assert classified_workflow_body["furthest_available_step"] == "planning"
    assert [
        (step["key"], step["status"], step["can_view"])
        for step in classified_workflow_body["steps"]
        ] == [
            ("client", "completed", True),
            ("duimp", "completed", True),
        ("context", "completed", True),
        ("purposes", "completed", True),
        ("planning", "current", True),
        ("drafts", "blocked", False),
        ("xml", "blocked", False),
        ("review", "blocked", False),
    ]

    plan_response = client.post(
        f"/import-processes/{process_id}/nfe-document-plan",
        headers=headers,
        json={"duimp_snapshot_id": snapshot_id},
    )
    assert plan_response.status_code == 201, plan_response.get_json()
    plan = plan_response.get_json()
    assert plan["master"] == {
        "type": "managerial",
        "is_fiscal_document": False,
        "has_number": False,
        "has_access_key": False,
        "has_xml": False,
    }
    assert plan["totals"]["documents_count"] == 1
    assert plan["documents"][0]["mixed_import_purposes"] is True
    assert plan["documents"][0]["operation_nature"] == (
        "Importação de mercadorias"
    )

    planned_workflow = client.get(
        f"/import-processes/{process_id}/nfe-workflow-state",
        headers=headers,
        query_string={
            "import_purpose": "resale",
            "environment": "production",
            "series": "1",
        },
    )
    assert planned_workflow.status_code == 200
    planned_workflow_body = planned_workflow.get_json()
    assert planned_workflow_body["next_action"] == "create_draft"
    assert planned_workflow_body["current_step"] == "drafts"
    assert planned_workflow_body["prerequisites"]["has_document_plan"] is True
    assert planned_workflow_body["prerequisites"]["planned_documents_count"] == 1
    assert [
        (step["key"], step["status"], step["can_view"])
        for step in planned_workflow_body["steps"]
    ] == [
        ("client", "completed", True),
        ("duimp", "completed", True),
        ("context", "completed", True),
        ("purposes", "completed", True),
        ("planning", "completed", True),
        ("drafts", "current", True),
        ("xml", "blocked", False),
        ("review", "blocked", False),
    ]


def test_document_plan_groups_exporters_and_reconciles_shared_costs(api):
    client, headers, importer_id = api

    profile = client.put(
        f"/clients/{importer_id}/fiscal-profile",
        headers=headers,
        json={
            "legal_name": "Importadora Teste Ltda",
            "cnpj": "00000000000191",
            "state_registration": "1234567890",
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

    rule_ids = {}
    for purpose, cfop in (("resale", "3102"), ("use_consumption", "3556")):
        response = client.post(
            f"/clients/{importer_id}/import-tax-rules",
            headers=headers,
            json={
                "name": f"PR {purpose}",
                "issuer_state": "PR",
                "import_purpose": purpose,
                "import_modality": "direct",
                "tax_regime": "3",
                "priority": 100,
                "configuration_json": {
                    "cfop": cfop,
                    "icms_rate": "12",
                    "icms_origin": "1",
                    "icms_cst": "90",
                    "ipi_cst": "49",
                    "pis_cst": "98",
                    "cofins_cst": "98",
                },
            },
        )
        assert response.status_code == 201, response.get_json()
        rule_ids[purpose] = response.get_json()["id"]

    process_response = client.post(
        "/import-processes",
        headers=headers,
        json={
            "importer_id": importer_id,
            "reference_code": "PLANO-MULTI-EXPORTADOR",
            "duimp_number": "26BR0000777777-1",
            "source": "manual",
        },
    )
    assert process_response.status_code == 201
    process_id = process_response.get_json()["id"]

    exporter_a = {
        "code": "EXP-A",
        "name": "Exporter Alpha Ltd",
        "foreign_tax_id": "ALPHA-001",
        "country_code": "1600",
        "country_name": "CHINA",
    }
    exporter_b = {
        "code": "EXP-B",
        "name": "Exporter Beta GmbH",
        "foreign_tax_id": "BETA-002",
        "country_code": "0230",
        "country_name": "ALEMANHA",
    }
    raw_items = [
        {
            "numeroItem": "1",
            "codigoProduto": "A-REV",
            "descricao": "Produto Alpha para revenda",
            "ncm": "87087090",
            "quantidade": "1",
            "unidade": "UN",
            "valorProduto": "300.00",
            "valorAduaneiro": "300.00",
            "codigoExportador": "EXP-A",
            "exporter": exporter_a,
        },
        {
            "numeroItem": "2",
            "codigoProduto": "A-USO",
            "descricao": "Produto Alpha para uso",
            "ncm": "84212300",
            "quantidade": "1",
            "unidade": "UN",
            "valorProduto": "100.00",
            "valorAduaneiro": "100.00",
            "codigoExportador": "EXP-A",
            "exporter": exporter_a,
        },
        {
            "numeroItem": "3",
            "codigoProduto": "B-REV",
            "descricao": "Produto Beta para revenda",
            "ncm": "87087090",
            "quantidade": "1",
            "unidade": "UN",
            "valorProduto": "100.00",
            "valorAduaneiro": "100.00",
            "codigoExportador": "EXP-B",
            "exporter": exporter_b,
        },
    ]
    snapshot_response = client.post(
        f"/import-processes/{process_id}/duimp-snapshots",
        headers=headers,
        json={
            "duimp_number": "26BR0000777777-1",
            "duimp_version": "1",
            "raw_payload": {
                "numero": "26BR0000777777-1",
                "versao": "1",
                "dataRegistro": "2026-08-18",
                "localDesembaraco": "PORTO DE PARANAGUA",
                "ufDesembaraco": "PR",
                "dataDesembaraco": "2026-08-18",
                "viaTransporteCodigo": "1",
                "modalidadeImportacao": "direct",
                "foreignSupplier": exporter_a,
                "itens": raw_items,
            },
        },
    )
    assert snapshot_response.status_code == 201, snapshot_response.get_json()
    snapshot_id = snapshot_response.get_json()["id"]

    classification = client.put(
        f"/import-processes/{process_id}/item-classifications",
        headers=headers,
        json={
            "duimp_snapshot_id": snapshot_id,
            "items": [
                {
                    "duimp_item_number": "1",
                    "import_purpose": "resale",
                    "tax_rule_id": rule_ids["resale"],
                },
                {
                    "duimp_item_number": "2",
                    "import_purpose": "use_consumption",
                    "tax_rule_id": rule_ids["use_consumption"],
                },
                {
                    "duimp_item_number": "3",
                    "import_purpose": "resale",
                    "tax_rule_id": rule_ids["resale"],
                },
            ],
        },
    )
    assert classification.status_code == 200, classification.get_json()
    assert classification.get_json()["ready_for_draft"] is True

    created = client.post(
        f"/import-processes/{process_id}/nfe-document-plan",
        headers=headers,
        json={
            "duimp_snapshot_id": snapshot_id,
            "additional_costs": {
                "afrmm": "0",
                "siscomex_fee": "0",
                "thc": "0",
                "other": "0.07",
            },
        },
    )
    assert created.status_code == 201, created.get_json()
    plan = created.get_json()
    assert plan["allocation_basis"] == "customs_value"
    assert plan["totals"] == {
        "documents_count": 2,
        "items_count": 3,
        "customs_value": "500.00",
        "shared_costs": "0.07",
        "planned_value": "500.07",
    }
    assert plan["reconciliation"]["balanced"] is True

    documents = {document["exporter_code"]: document for document in plan["documents"]}
    assert set(documents) == {"EXP-A", "EXP-B"}
    assert documents["EXP-A"]["items_count"] == 2
    assert documents["EXP-A"]["mixed_import_purposes"] is True
    assert documents["EXP-A"]["operation_nature"] == "Importação de mercadorias"
    assert documents["EXP-A"]["item_purposes"] == ["resale", "use_consumption"]
    alpha_items = {
        item["duimp_item_number"]: item
        for item in documents["EXP-A"]["items"]
    }
    assert alpha_items["1"]["allocated_shared_costs"]["other"] == "0.05"
    assert alpha_items["2"]["allocated_shared_costs"]["other"] == "0.01"
    assert documents["EXP-B"]["items"][0]["allocated_shared_costs"]["other"] == "0.01"

    fetched = client.get(
        f"/import-processes/{process_id}/nfe-document-plan",
        headers=headers,
        query_string={"duimp_snapshot_id": snapshot_id},
    )
    assert fetched.status_code == 200
    assert fetched.get_json()["plan"]["id"] == plan["id"]

    workflow = client.get(
        f"/import-processes/{process_id}/nfe-workflow-state",
        headers=headers,
        query_string={
            "import_purpose": "resale",
            "environment": "production",
            "series": "1",
        },
    )
    assert workflow.status_code == 200, workflow.get_json()
    workflow_body = workflow.get_json()
    assert workflow_body["next_action"] == "create_child_drafts"
    assert workflow_body["current_step"] == "drafts"
    assert workflow_body["prerequisites"]["planned_documents_count"] == 2

    generated = client.post(
        f"/import-processes/{process_id}/nfe-document-plan/generate-drafts",
        headers=headers,
        json={
            "duimp_snapshot_id": snapshot_id,
            "environment": "production",
            "series": "1",
        },
    )
    assert generated.status_code == 201, generated.get_json()
    generated_body = generated.get_json()
    assert len(generated_body["created_draft_ids"]) == 2
    assert generated_body["plan"]["progress"]["all_drafts_created"] is True
    child_documents = generated_body["plan"]["documents"]
    assert all(document["draft"] for document in child_documents)
    assert len({document["draft"]["id"] for document in child_documents}) == 2

    replayed = client.post(
        f"/import-processes/{process_id}/nfe-document-plan/generate-drafts",
        headers=headers,
        json={
            "duimp_snapshot_id": snapshot_id,
            "environment": "production",
            "series": "1",
        },
    )
    assert replayed.status_code == 200, replayed.get_json()
    assert replayed.get_json()["created_draft_ids"] == []
    assert len(replayed.get_json()["reused_draft_ids"]) == 2

    drafts = client.get(
        f"/import-processes/{process_id}/nfe-drafts",
        headers=headers,
    ).get_json()["items"]
    assert len(drafts) == 2
    assert {draft["items_count"] for draft in drafts} == {1, 2}

    sequence = client.put(
        f"/clients/{importer_id}/nfe-number-sequences",
        headers=headers,
        json={
            "environment": "production",
            "model": "55",
            "series": "1",
            "current_number": 100,
        },
    )
    assert sequence.status_code == 200, sequence.get_json()

    xml_batch = client.post(
        f"/import-processes/{process_id}/nfe-document-plan/generate-xmls",
        headers=headers,
        json={"duimp_snapshot_id": snapshot_id},
    )
    assert xml_batch.status_code == 200, xml_batch.get_json()
    xml_body = xml_batch.get_json()
    assert len(xml_body["results"]) == 2
    assert xml_body["all_valid"] is True
    assert all(result.get("xml_version_id") for result in xml_body["results"]), xml_body
    xml_documents = xml_body["plan"]["documents"]
    assert len({document["draft"]["number"] for document in xml_documents}) == 2
    assert len({document["draft"]["access_key"] for document in xml_documents}) == 2

    bundle = client.get(
        f"/import-processes/{process_id}/nfe-document-plan/xmls/download",
        headers=headers,
        query_string={"duimp_snapshot_id": snapshot_id},
    )
    assert bundle.status_code == 200
    assert bundle.content_type == "application/zip"

    completed_workflow = client.get(
        f"/import-processes/{process_id}/nfe-workflow-state",
        headers=headers,
        query_string={
            "import_purpose": "resale",
            "environment": "production",
            "series": "1",
        },
    )
    assert completed_workflow.status_code == 200
    assert completed_workflow.get_json()["next_action"] == "completed"

    legacy_draft = client.post(
        f"/import-processes/{process_id}/nfe-draft/from-duimp",
        headers=headers,
        json={
            "environment": "production",
            "series": "1",
            "import_purpose": "resale",
            "duimp_snapshot_id": snapshot_id,
        },
    )
    assert legacy_draft.status_code == 400
    assert "múltiplos exportadores" in legacy_draft.get_json()["message"]


def test_provider_connection_post_reactivates_existing_scope(api):
    client, headers, _ = api
    payload = {
        "provider": "portal_unico",
        "environment": "production",
        "auth_type": "api_key",
        "status": "inactive",
        "credentials_ref": "gcp:PORTAL_UNICO_OLD",
        "config_json": {"role_type": "IMPEXP"},
    }

    created = client.post(
        "/external-provider-connections",
        headers=headers,
        json=payload,
    )
    assert created.status_code == 201
    connection_id = created.get_json()["id"]

    payload.update(
        {
            "status": "active",
            "credentials_ref": "gcp:PORTAL_UNICO",
        }
    )
    updated = client.post(
        "/external-provider-connections",
        headers=headers,
        json=payload,
    )
    assert updated.status_code == 201
    assert updated.get_json()["id"] == connection_id
    assert updated.get_json()["status"] == "ACTIVE"
    assert updated.get_json()["credentials_ref"] == "gcp:PORTAL_UNICO"

    listed = client.get(
        (
            "/external-provider-connections"
            "?provider=portal_unico&environment=production&status=active"
        ),
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.get_json()["total"] == 1

def test_process_dashboard_groups_by_client_and_exposes_next_action(
    api,
    monkeypatch,
):
    client, headers, importer_id = api

    existing_client = db.session.get(Client, UUID(importer_id))
    second_client = Client(
        organization_id=existing_client.organization_id,
        cnpj="11222333000181",
        razao_social="Cliente Painel Aduaneiro Ltda",
        nome_resumido="Cliente Painel",
        ativo=True,
    )
    db.session.add(second_client)
    db.session.commit()

    created = client.post(
        "/import-processes",
        headers=headers,
        json={
            "importer_id": str(second_client.id),
            "source": "portal_unico",
        },
    )
    assert created.status_code == 201

    with monkeypatch.context() as patch:
        patch.setattr(
            ImportNfeService,
            "build_import_process_list_summary",
            lambda *_args, **_kwargs: pytest.fail(
                "O agrupamento não deve recalcular o workflow por processo."
            ),
        )
        grouped = client.get(
            (
                "/import-processes/client-groups"
                "?q=11222333000181&created_by_me=true"
            ),
            headers=headers,
        )
    assert grouped.status_code == 200
    grouped_body = grouped.get_json()
    assert grouped_body["total"] == 1
    assert grouped_body["items"] == [
        {
            "client_id": str(second_client.id),
            "name": "Cliente Painel",
            "legal_name": "Cliente Painel Aduaneiro Ltda",
            "cnpj": "11222333000181",
            "process_count": 1,
            "pending_count": 1,
            "last_updated_at": grouped_body["items"][0]["last_updated_at"],
        }
    ]

    processes = client.get(
        f"/import-processes?importer_id={second_client.id}",
        headers=headers,
    )
    assert processes.status_code == 200
    process = processes.get_json()["items"][0]
    assert process["importer"]["name"] == "Cliente Painel"
    assert process["reference_code"].startswith("NFE-")
    assert process["duimp_number"] is None
    assert process["next_action"] == "configure_fiscal_profile"
    assert process["pending"] is True
    assert process["planned_documents_count"] == 0
    assert process["last_responsible"]["name"] == "Operador Teste"
    assert process["last_responsible"]["is_current_user"] is True


def test_number_sequence_preserves_progress_and_rejects_regression(api):
    client, headers, importer_id = api
    created = client.put(
        f"/clients/{importer_id}/nfe-number-sequences",
        headers=headers,
        json={
            "environment": "production",
            "model": "55",
            "series": "1",
            "current_number": 100,
            "initial_number": 1,
            "status": "active",
        },
    )
    assert created.status_code == 200, created.get_json()
    assert created.get_json()["environment"] == "production"
    assert created.get_json()["status"] == "active"

    listed = client.get(
        f"/clients/{importer_id}/nfe-number-sequences",
        headers=headers,
    )
    assert listed.status_code == 200, listed.get_json()
    assert listed.get_json()[0]["environment"] == "production"
    assert listed.get_json()[0]["status"] == "active"

    preserved = client.put(
        f"/clients/{importer_id}/nfe-number-sequences",
        headers=headers,
        json={
            "environment": "production",
            "model": "55",
            "series": "1",
            "initial_number": 1,
            "status": "active",
        },
    )
    assert preserved.status_code == 200, preserved.get_json()
    assert preserved.get_json()["current_number"] == 100

    rejected = client.put(
        f"/clients/{importer_id}/nfe-number-sequences",
        headers=headers,
        json={
            "environment": "production",
            "model": "55",
            "series": "1",
            "current_number": 99,
            "initial_number": 1,
            "status": "active",
        },
    )
    assert rejected.status_code == 409
    assert rejected.get_json()["error"] == "nfe_sequence_regression"
    assert rejected.get_json()["minimum_safe_number"] == 100
