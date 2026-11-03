from app.extensions import db
from uuid import UUID
from app.models import Client, ImportProcess, NfeItemClassification
from tests.integration.test_import_nfe_api_flow import api  # noqa: F401 - shared isolated SQLite fixture


def config(*, code="A-001", description="Produto padrão"):
    return {
        "document": {"operation_nature": "Compra para industrialização"},
        "transport": {"carrier_name": ""},
        "additional_info": {},
        "items": [{
            "reference": code, "product_code": "INTERNO-1", "description": description,
            "ncm": "39172290", "cfop": "3101", "unit": "UN",
            "import_purpose": "industrialization",
            "tax_configuration": {"icms_cst": "00", "icms_rate": "12", "ii_rate": "10.8"},
        }],
    }


def test_client_config_is_reviewed_before_saving_and_copied_per_process(api):
    client, headers, client_id = api
    url = f"/clients/{client_id}/simplified-issuance"
    bad = config()
    bad["items"][0]["ncm"] = "invalid"
    assert client.put(url, headers=headers, json=bad).status_code == 400
    assert client.get(url, headers=headers).get_json()["config"] is None

    saved = client.put(url, headers=headers, json=config())
    assert saved.status_code == 200, saved.get_json()
    process_response = client.post(f"{url}/start", headers=headers)
    assert process_response.status_code == 201, process_response.get_json()
    process_id = process_response.get_json()["id"]
    assert process_response.get_json()["source"].lower() == "simplified"
    assert "simplified_config_snapshot" not in process_response.get_json()

    assert client.put(url, headers=headers, json=config(description="Outro modelo")).status_code == 200
    with client.application.app_context():
        process = db.session.get(ImportProcess, UUID(process_id))
        assert process.simplified_config_snapshot["items"][0]["description"] == "Produto padrão"
        assert db.session.get(Client, UUID(client_id)).simplified_issuance_config["items"][0]["description"] == "Outro modelo"


def test_duimp_only_matches_exact_code_and_ncm_and_preserves_template_tax(api):
    client, headers, client_id = api
    url = f"/clients/{client_id}/simplified-issuance"
    client.put(url, headers=headers, json=config())
    process_id = client.post(f"{url}/start", headers=headers).get_json()["id"]
    snapshot = client.post(f"/import-processes/{process_id}/duimp-snapshots", headers=headers, json={
        "duimp_number": "26BR00000000001", "raw_payload": {"numero": "26BR00000000001"},
        "normalized_payload": {"number": "26BR00000000001", "items": [
            {"number": "00001", "catalog_product_code": "A-001", "ncm": "39172290", "description": "Descrição do Portal"},
            {"number": "00002", "catalog_product_code": "OUTRO", "ncm": "39172290", "description": "Outra mercadoria"},
        ]},
    })
    assert snapshot.status_code == 201, snapshot.get_json()
    check = client.get(f"/import-processes/{process_id}/simplified-reconciliation", headers=headers)
    assert [item["status"] for item in check.get_json()["items"]] == ["matched", "unmatched"]
    with client.application.app_context():
        row = NfeItemClassification.query.filter_by(import_process_id=UUID(process_id), duimp_item_number="00001").one()
        assert str(getattr(row.source, "value", row.source)) == "simplified"
        assert row.tax_rule_snapshot["configuration_json"]["icms_rate"] == "12"
        assert row.cfop == "3101"

    linked = client.put(f"/import-processes/{process_id}/simplified-reconciliation", headers=headers, json={"links": {"00002": 0}})
    assert linked.status_code == 200, linked.get_json()
    assert linked.get_json()["items"][1]["status"] == "linked"
    invalid = client.put(f"/import-processes/{process_id}/simplified-reconciliation", headers=headers, json={"links": {"00002": 999}})
    assert invalid.status_code == 400


def test_simplified_draft_uses_client_code_and_tax_without_number(api):
    client, headers, client_id = api
    profile = client.put(f"/clients/{client_id}/fiscal-profile", headers=headers, json={
        "legal_name": "Importadora Teste Ltda", "cnpj": "00000000000191",
        "state_registration": "1234567890", "tax_regime": "3", "street": "Rua de Teste",
        "number": "100", "district": "Centro", "city_code": "4106902",
        "city_name": "Curitiba", "state": "PR", "zip_code": "80000000",
        "country_code": "1058", "country_name": "Brasil",
    })
    assert profile.status_code == 200
    client.put(f"/clients/{client_id}/simplified-issuance", headers=headers, json=config(code="PROD-001"))
    process_id = client.post(f"/clients/{client_id}/simplified-issuance/start", headers=headers).get_json()["id"]
    response = client.post(f"/import-processes/{process_id}/nfe-draft/from-duimp", headers=headers, json={
        "duimp_payload": {
            "numero": "26BR0000000000-1", "versao": "1", "dataRegistro": "2026-07-14",
            "localDesembaraco": "0917800 - PORTO DE PARANAGUA", "ufDesembaraco": "PR",
            "dataDesembaraco": "2026-07-15", "viaTransporteCodigo": "1", "tipoIntermedio": "1",
            "modalidadeImportacao": "direct", "itens": [{
                "numeroItem": "1", "codigoProduto": "PROD-001", "descricao": "Descrição do Portal",
                "ncm": "39172290", "quantidade": "12", "unidade": "UN", "valorProduto": "6054.39",
                "valorUnitario": "504.5325", "sequenciaAdicao": "1", "codigoFabricante": "0000",
                "codigoExportador": "EXP-TESTE-001",
                "tributos": {"ii": {"base": "6054.39", "rate": "10.8", "value": "653.87"}},
            }],
        },
        "foreign_supplier": {"name": "Foreign Supplier Ltd", "country_code": "1600",
            "country_name": "CHINA", "address": {"street": "EXTERIOR", "number": "0",
            "district": "EXTERIOR", "city_name": "EXTERIOR"}},
    })
    assert response.status_code == 201, response.get_json()
    draft = response.get_json()["draft"]
    assert draft["number"] is None
    with client.application.app_context():
        stored = db.session.get(__import__("app.models", fromlist=["NfeDraft"]).NfeDraft, UUID(draft["id"]))
        assert stored.fiscal_payload["items"][0]["product_code"] == "INTERNO-1"
        assert stored.fiscal_payload["items"][0]["description"] == "Produto padrão"
        assert stored.fiscal_payload["source"]["simplified_issuance"] is True
