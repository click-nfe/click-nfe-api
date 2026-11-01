from copy import deepcopy
from decimal import Decimal

import pytest

from app.services.duimp_normalizer import DuimpNormalizer


def portal_payload():
    return {
        "provider": "portal_unico",
        "numero": "26BR0000000000-1",
        "versao": "1",
        "dadosGerais": {
            "identificacao": {
                "numero": "26BR00000000001",
                "versao": "1",
                "dataRegistro": "2026-07-14T17:33:00-0300",
                "importador": {"tipoImportador": "CNPJ", "ni": "00000000000191"},
            },
            "carga": {
                "unidadeDeclarada": {"codigo": "0917800", "uf": "PR"},
                "paisProcedencia": {"codigo": "CN", "descricao": "China"},
                "multiplosConhecimentosCarga": {
                    "cargasReferenciadas": [
                        {"dadosAfrmmTum": {"valorDevido": 12.34}}
                    ]
                },
            },
            "quantidadeItens": 1,
            "tributos": {
                "tributosCalculados": [
                    {
                        "tipo": "II",
                        "valoresBRL": {"devido": 180},
                        "memoriaCalculo": {"baseCalculoBRL": 1000},
                    },
                    {
                        "tipo": "TAXA_UTILIZACAO_SISCOMEX",
                        "valoresBRL": {
                            "aRecolher": 154.23,
                            "recolhido": 154.23,
                        },
                    },
                ]
            },
        },
        "itens": [
            {
                "identificacao": {
                    "numero": "26BR00000000001",
                    "versao": "1",
                    "numeroItem": 1,
                },
                "produto": {
                    "codigo": 215,
                    "versao": "1",
                    "ncm": "87087090",
                    "descricao": "Roda automotiva",
                },
                "caracterizacaoImportacao": {"indicador": "IMPORTACAO_DIRETA"},
                "mercadoria": {
                    "unidadeComercial": "PECAS",
                    "quantidadeComercial": 12,
                    "pesoLiquido": 25.5,
                    "descricao": "Modelo de teste",
                },
                "condicaoVenda": {
                    "valorBRL": 900,
                    "frete": {"valorBRL": 90},
                    "seguro": {"valorBRL": 10},
                },
                "exportador": {
                    "codigo": "EXP-1",
                    "nome": "Fornecedor Exterior",
                    "pais": {"codigo": "CN", "descricao": "China"},
                },
                "fabricante": {"codigo": "FAB-1"},
                "tributos": {
                    "mercadoria": {"valorAduaneiroBRL": 1000},
                    "tributosCalculados": [
                        {
                            "tipo": "II",
                            "valoresBRL": {"devido": 180},
                            "memoriaCalculo": {
                                "baseCalculoBRL": 1000,
                                "valorAliquota": 18,
                            },
                        }
                    ],
                },
                "cClassTrib": "000001",
            }
        ],
    }


def test_normalizes_official_portal_payload_separating_fiscal_components():
    result = DuimpNormalizer().normalize(portal_payload())

    assert result["number"] == "26BR0000000000-1"
    assert result["api_number"] == "26BR00000000001"
    assert result["registration_date"] == "2026-07-14"
    assert result["clearance_location_code"] == "0917800"
    assert result["clearance_state"] == "PR"
    assert result["afrmm_value"] == "12.34"
    assert result["import_modality"] == "direct"
    assert result["foreign_supplier"]["name"] == "Fornecedor Exterior"
    assert result["tax_totals"]["ii"]["value"] == "180"
    assert (
        result["tax_totals"]["taxa_utilizacao_siscomex"]["value"]
        == "154.23"
    )

    item = result["items"][0]
    assert item["customs_value"] == "1000"
    assert item["product_value"] == "900"
    assert Decimal(item["unit_value"]) == Decimal("75")
    assert item["freight_value"] == "90"
    assert item["insurance_value"] == "10"
    assert item["customs_freight_value"] == "90"
    assert item["customs_insurance_value"] == "10"
    assert item["net_weight"] == "25.5"
    assert item["taxes"]["ii"] == {
        "value": "180",
        "base": "1000",
        "rate": "18",
        "calculation": {"baseCalculoBRL": 1000, "valorAliquota": 18},
        "raw": {
            "tipo": "II",
            "valoresBRL": {"devido": 180},
            "memoriaCalculo": {"baseCalculoBRL": 1000, "valorAliquota": 18},
        },
    }
    assert item["tax_classification_code"] == "000001"


def test_normalizes_catalog_enrichment_into_fiscal_product_fields():
    payload = portal_payload()
    payload["catalogEnrichment"] = {
        "products_requested": 1,
        "products_enriched": 1,
        "operators_requested": 1,
        "operators_enriched": 1,
        "failures": [],
    }
    payload["itens"][0]["produto"].update(
        {
            "descricao": None,
            "denominacao": "Roda automotiva detalhada",
            "codigoInternoNfe": "PROD-INT-001",
        }
    )
    payload["itens"][0]["exportador"] = {
        "codigo": "OPE_TEST_1",
        "versao": "1",
        "nome": "FOREIGN SUPPLIER TEST LTD",
        "tin": "FOREIGN-TAX-ID-001",
        "logradouro": "TEST STREET 100",
        "nomeCidade": "TEST CITY",
        "codigoPais": "CN",
    }

    result = DuimpNormalizer().normalize(payload)

    assert result["catalog_enrichment"]["products_enriched"] == 1
    assert result["items"][0]["product_code"] == ""
    assert result["items"][0]["catalog_product_code"] == "PROD-INT-001"
    assert result["items"][0]["catalog_product_id"] == "215"
    assert result["items"][0]["description"] == "Roda automotiva detalhada"
    assert result["foreign_supplier"]["foreign_tax_id"] == (
        "FOREIGN-TAX-ID-001"
    )
    assert result["foreign_supplier"]["country_iso_alpha_2"] == "CN"
    assert result["foreign_supplier"]["address"] == {
        "logradouro": "TEST STREET 100",
        "city_name": "TEST CITY",
    }


def test_normalizes_duimp_additions_units_description_and_missing_manufacturer():
    payload = portal_payload()
    payload["dadosGerais"]["adicoes"] = [
        {"numero": 1, "itens": [1]},
        {"numero": 2, "itens": [2]},
    ]
    first = payload["itens"][0]
    first["mercadoria"]["unidadeComercial"] = "UNIDADE"
    first["fabricante"] = {"codigo": None, "pais": {"codigo": "US"}}
    first["produto"]["catalogo"] = {
        "descricao": "DESCRICAO COMPLETA " + ("TECNICA " * 30)
    }
    second = deepcopy(first)
    second["identificacao"] = {"numeroItem": 2}
    payload["itens"].append(second)

    result = DuimpNormalizer().normalize(payload)

    first_result, second_result = result["items"]
    assert first_result["addition_number"] is None
    assert first_result["sequence_number"] == "1"
    assert second_result["addition_number"] is None
    assert second_result["sequence_number"] == "2"
    assert second_result["number"] == "2"
    assert first_result["commercial_unit"] == "UN"
    assert first_result["taxable_unit"] == "UN"
    assert len(first_result["description"]) <= 120
    assert len(first_result["additional_info"]) > len(first_result["description"])
    assert first_result["manufacturer_code"] is None
    assert first_result["manufacturer_code_missing_from_portal"] is True


def test_rejects_mixed_modalities_in_same_duimp():
    payload = portal_payload()
    second = dict(payload["itens"][0])
    second["identificacao"] = {"numeroItem": 2}
    second["caracterizacaoImportacao"] = {
        "indicador": "IMPORTACAO_POR_ENCOMENDA",
        "ni": "00000000000272",
    }
    payload["itens"].append(second)

    try:
        DuimpNormalizer().normalize(payload)
    except ValueError as exc:
        assert "modalidades de importação diferentes" in str(exc)
    else:
        raise AssertionError("Era esperado ValueError para modalidades diferentes")


def test_normalizes_multiple_exporters_for_document_planning():
    payload = portal_payload()
    second = deepcopy(payload["itens"][0])
    second["identificacao"] = {"numeroItem": 2}
    second["exportador"] = {
        "codigo": "EXP-2",
        "nome": "Segundo Fornecedor Exterior",
        "tin": "FOREIGN-002",
        "pais": {"codigo": "DE", "descricao": "Alemanha"},
    }
    payload["itens"].append(second)

    result = DuimpNormalizer().normalize(payload)

    assert result["exporter_count"] == 2
    assert [row["code"] for row in result["foreign_suppliers"]] == [
        "EXP-1",
        "EXP-2",
    ]
    assert result["foreign_supplier"]["code"] == "EXP-1"
    assert result["items"][1]["exporter_code"] == "EXP-2"


@pytest.mark.parametrize(
    ("portal_value", "expected"),
    [
        ("IMPORTACAO_DIRETA", "direct"),
        ("IMPORTACAO_POR_CONTA_E_ORDEM", "on_behalf"),
        ("IMPORTACAO_POR_ENCOMENDA", "by_order"),
    ],
)
def test_maps_supported_import_modalities(portal_value, expected):
    payload = portal_payload()
    payload["itens"][0]["caracterizacaoImportacao"] = {
        "indicador": portal_value,
        "ni": "00000000000272" if expected != "direct" else None,
    }

    result = DuimpNormalizer().normalize(payload)

    assert result["import_modality"] == expected
    if expected != "direct":
        assert result["third_party_tax_id"] == "00000000000272"

def test_portal_catalog_code_is_not_assumed_to_be_nfe_cprod():
    payload = portal_payload()
    payload["itens"][0]["produto"]["codigo"] = 204
    payload["itens"][0]["produto"]["catalogo"] = {
        "codigosInterno": ["Gray Pipe Gray"]
    }

    item = DuimpNormalizer().normalize(payload)["items"][0]

    assert item["catalog_product_id"] == "204"
    assert item["catalog_product_code"] == "Gray Pipe Gray"
    assert item["product_code"] == ""

