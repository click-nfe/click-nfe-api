import pytest

from app.extensions import db
from uuid import UUID
from app.models import Client, ImportProcess, NfeItemClassification
from tests.integration.test_import_nfe_api_flow import api  # noqa: F401 - shared isolated SQLite fixture
from app.services.simplified_issuance import normalize_config, draft_defaults, reconcile


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


def test_named_templates_are_isolated_and_process_keeps_selected_snapshot(api):
    client, headers, client_id = api
    base = f"/clients/{client_id}/simplified-issuance/templates"
    first = client.post(base, headers=headers, json={"name": "Revenda", "description": "Produtos para revenda", "config": config(description="Padrão")})
    second = client.post(base, headers=headers, json={"name": "Industrialização", "description": "", "config": config(description="Outro")})
    assert first.status_code == second.status_code == 201
    first_id, second_id = first.get_json()["id"], second.get_json()["id"]
    assert {item["name"] for item in client.get(base, headers=headers).get_json()["items"]} == {"Revenda", "Industrialização"}
    started = client.post(f"/clients/{client_id}/simplified-issuance/start", headers=headers, json={"template_id": first_id, "duimp_number": "26BR0000000000-1"})
    assert started.status_code == 201, started.get_json()
    assert client.put(f"{base}/{first_id}", headers=headers, json={"name": "Revenda editada", "description": "", "config": config(description="Alterado")}).status_code == 200
    with client.application.app_context():
        process = db.session.get(ImportProcess, UUID(started.get_json()["id"]))
        assert process.simplified_config_snapshot["items"][0]["description"] == "Padrão"
    assert client.delete(f"{base}/{second_id}", headers=headers).status_code == 204
    assert len(client.get(base, headers=headers).get_json()["items"]) == 1
    assert client.post(f"/clients/{client_id}/simplified-issuance/start", headers=headers, json={"template_id": second_id}).status_code == 404


def test_one_off_spreadsheet_configuration_does_not_change_client_default(api):
    client, headers, client_id = api
    response = client.post(f"/clients/{client_id}/simplified-issuance/start", headers=headers, json={"config": config(description="Somente esta operação")})
    assert response.status_code == 201, response.get_json()
    with client.application.app_context():
        assert db.session.get(Client, UUID(client_id)).simplified_issuance_config is None
        process = db.session.get(ImportProcess, UUID(response.get_json()["id"]))
        assert process.simplified_config_snapshot["items"][0]["description"] == "Somente esta operação"


def test_spreadsheet_manual_items_skip_portal_and_reject_incomplete_values(api):
    client, headers, client_id = api
    profile = client.put(f"/clients/{client_id}/fiscal-profile", headers=headers, json={
        "legal_name": "Importadora Teste Ltda", "cnpj": "00000000000191", "state_registration": "1234567890", "tax_regime": "3",
        "street": "Rua Teste", "number": "100", "district": "Centro", "city_code": "4106902", "city_name": "Curitiba", "state": "PR", "zip_code": "80000000",
        "country_code": "1058", "country_name": "Brasil",
    })
    assert profile.status_code == 200
    sequence = client.put(f"/clients/{client_id}/nfe-number-sequences", headers=headers, json={"environment": "production", "model": "55", "series": "1", "current_number": 10})
    assert sequence.status_code == 200
    number = "26BR0000000000-1"
    process = client.post(f"/clients/{client_id}/simplified-issuance/start", headers=headers, json={"duimp_number": number, "config": config()})
    assert process.status_code == 201
    url = f"/import-processes/{process.get_json()['id']}/simplified-prepare"
    payload = {"numero": number, "dataRegistro": "2026-10-28", "localDesembaraco": "Paranaguá", "ufDesembaraco": "PR", "dataDesembaraco": "2026-10-29", "viaTransporteCodigo": "1", "fornecedorEstrangeiro": {"name": "Fornecedor", "country_code": "1600", "country_name": "CHINA"}, "itens": [{"numeroItem": "1", "sequenciaAdicao": "1", "codigoProduto": "A-001", "descricao": "Produto", "ncm": "39172290", "unidade": "UN", "quantidade": "2", "valorProduto": "100", "valorUnitario": "50", "codigoExportador": "EXP-1"}]}
    invalid = {**payload, "itens": [{**payload["itens"][0], "valorProduto": ""}]}
    assert client.post(url, headers=headers, json={"manual_payload": invalid, "sign": False}).status_code == 400
    response = client.post(url, headers=headers, json={"manual_payload": payload, "sign": False})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["process_id"] == process.get_json()["id"]
    assert response.get_json()["status"] == "validated", response.get_json()
    with client.application.app_context():
        process_row = db.session.get(ImportProcess, UUID(process.get_json()["id"]))
        from app.models import DuimpSnapshot
        snapshot = DuimpSnapshot.query.filter_by(import_process_id=process_row.id).one()
        assert getattr(snapshot.source_provider, "value", snapshot.source_provider) == "manual_spreadsheet"
        assert snapshot.raw_payload["manual_source"] == "spreadsheet"


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


def test_reconciliation_prefers_duimp_product_column_in_old_snapshot():
    template = config(code="204")
    old_snapshot = {"items": [
        {"number": "00001", "catalog_product_id": "204", "catalog_product_code": "Gray Pipe Gray", "ncm": "39172290"},
        {"number": "00002", "catalog_product_id": "204", "catalog_product_code": "Gray Pipe Gray", "ncm": "39172290"},
    ]}
    matches = reconcile(template, old_snapshot)
    assert [entry["catalog_product_code"] for entry in matches] == ["204", "204"]
    assert [entry["status"] for entry in matches] == ["matched", "matched"]


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


def test_extended_workbook_fields_are_validated_and_applied_to_draft_defaults():
    template = config()
    template.update({"series": "1", "volume": {"quantity": "80", "species": "CAIXA", "gross_weight": "1500"},
                     "payment": {"method": "90", "value": "0"}})
    template["items"][0]["tax_configuration"].update({"icms_origin": "1", "ipi_cst": "49"})
    normalized = normalize_config(template)
    defaults = draft_defaults(normalized)
    assert defaults["transport"]["volume"] == {"quantity": 80, "species": "CAIXA", "gross_weight": "1500"}
    assert defaults["payment"] == {"method": "90", "value": "0"}
    assert normalized["items"][0]["tax_configuration"]["icms_origin"] == "1"


def test_icms_workbook_fields_validate_without_automatically_confirming_tax_treatment():
    template = config()
    taxes = template["items"][0]["tax_configuration"]
    taxes.update({"icms_st_base_method": "4", "icms_st_mva_rate": "40.5",
                  "icms_st_retained_base": "1050.00", "icms_tax_treatment_confirmed": False})
    normalized = normalize_config(template)
    assert normalized["items"][0]["tax_configuration"]["icms_tax_treatment_confirmed"] is False
    taxes["icms_st_base_method"] = "8"
    with pytest.raises(ValueError, match="Modalidade"):
        normalize_config(template)


def test_quick_prepare_reports_unmatched_items_without_creating_a_draft(api):
    client, headers, client_id = api
    url = f"/clients/{client_id}/simplified-issuance"
    assert client.put(url, headers=headers, json=config()).status_code == 200
    start = client.post(f"{url}/start", headers=headers, json={"duimp_number": "26BR0000000000-1"})
    assert start.status_code == 201
    process_id = start.get_json()["id"]
    prepared = client.post(f"/import-processes/{process_id}/simplified-prepare", headers=headers, json={"sign": False, "duimp_payload": {
        "numero": "26BR0000000000-1", "versao": "1", "itens": [
            {"numeroItem": "1", "codigoProduto": "A-001", "ncm": "39172290", "descricao": "Produto", "quantidade": "1", "valorProduto": "100"},
            {"numeroItem": "2", "codigoProduto": "OUTRO", "ncm": "39172290", "descricao": "Outro", "quantidade": "1", "valorProduto": "100"},
        ],
    }})
    assert prepared.status_code == 200, prepared.get_json()
    assert prepared.get_json()["status"] == "needs_review"
    assert prepared.get_json()["unmatched_items"]
