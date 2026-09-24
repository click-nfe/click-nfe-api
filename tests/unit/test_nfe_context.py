import pytest
from marshmallow import ValidationError

from app.nfe_reference import NFE_TRANSPORT_MODES
from app.schemas.nfe_automation import ResolveNfeContextSchema
from app.services.nfe_context import NfeContextResolver
from app.services.import_process import ImportNfeService


def test_context_resolver_combines_official_sources_and_explicit_overrides():
    resolver = NfeContextResolver()
    normalized = {
        "registration_date": "2026-05-26",
        "clearance_location_code": "0927800",
        "clearance_location": None,
        "clearance_state": None,
        "clearance_date": None,
        "transport_mode_code": None,
        "foreign_supplier": {"country_iso_alpha_2": "US"},
    }
    external = {
        "cargo_knowledge": [
            {
                "situacao": "A",
                "tipo": "AWB",
                "codigoAeroportoDestinoConhecimento": "CWB",
            }
        ],
        "customs_unit": {
            "dados": [
                {
                    "campos": [
                        {"nome": "NOME", "valor": "AEROPORTO DE CURITIBA"},
                        {"nome": "UF", "valor": "PR"},
                    ]
                }
            ]
        },
        "country": {
            "dados": [
                {
                    "campos": [
                        {"nome": "CODIGO", "valor": "2496"},
                        {"nome": "NOME", "valor": "ESTADOS UNIDOS"},
                    ]
                }
            ]
        },
        "icms_declaration": {
            "valorAfrmm": 0,
            "valorDespesasAduaneiras": 123.45,
        },
    }

    result = resolver.resolve(
        normalized=normalized,
        external=external,
        overrides={"clearance_date": "2026-05-30", "unknown": "ignored"},
    )

    assert result["ready_for_draft"] is True
    assert result["normalized"]["clearance_location"] == "AEROPORTO DE CURITIBA"
    assert result["normalized"]["clearance_state"] == "PR"
    assert result["normalized"]["transport_mode_code"] == "4"
    assert result["normalized"]["clearance_date"] == "2026-05-30"
    assert result["normalized"]["foreign_supplier"]["country_code"] == "2496"
    assert result["fields"]["clearance_location"]["source"] == "portal_unico_tabx"
    assert result["fields"]["clearance_date"]["source"] == "operator_override"
    assert result["suggested"]["additional_costs"] == {
        "afrmm": "0",
        "other": "123.45",
    }
    assert "unknown" not in result["normalized"]


def test_context_resolver_keeps_unconfirmed_fields_missing():
    result = NfeContextResolver().resolve(
        normalized={
            "registration_date": "2026-05-26",
            "foreign_supplier": {},
        }
    )

    assert result["ready_for_draft"] is False
    assert "clearance_date" in result["missing_fields"]
    assert "foreign_supplier.country_code" in result["missing_fields"]
    assert "foreign_supplier.country_name" in result["missing_fields"]
    assert result["fields"]["foreign_supplier.country_name"] == {
        "value": None,
        "source": None,
        "status": "missing",
    }
    assert result["fields"]["clearance_date"] == {
        "value": None,
        "source": None,
        "status": "missing",
    }


@pytest.mark.parametrize("transport_mode_code", NFE_TRANSPORT_MODES)
def test_context_accepts_every_transport_mode_from_nfe_schema(
    transport_mode_code,
):
    result = NfeContextResolver().resolve(
        normalized={"registration_date": "2026-05-26", "foreign_supplier": {}},
        overrides={"transport_mode_code": transport_mode_code},
    )

    assert result["normalized"]["transport_mode_code"] == transport_mode_code


def test_context_schema_rejects_unknown_transport_mode():
    with pytest.raises(ValidationError) as exc_info:
        ResolveNfeContextSchema().load(
            {"overrides": {"transport_mode_code": "14"}}
        )

    assert exc_info.value.messages == {
        "overrides": {
            "transport_mode_code": [
                "Selecione uma via de transporte válida (códigos 1 a 13)."
            ]
        }
    }


def test_context_marks_unknown_transport_mode_as_missing():
    result = NfeContextResolver().resolve(
        normalized={
            "registration_date": "2026-05-26",
            "transport_mode_code": "14",
            "foreign_supplier": {},
        }
    )

    assert result["normalized"]["transport_mode_code"] is None
    assert "transport_mode_code" in result["missing_fields"]


def test_context_uses_controlled_official_references_without_tabx():
    result = NfeContextResolver().resolve(
        normalized={
            "registration_date": "2026-05-26",
            "clearance_location_code": "0927800",
            "clearance_date": "2026-05-27",
            "transport_mode_code": "4",
            "foreign_supplier": {"country_iso_alpha_2": "US"},
        },
        external={
            "cached_customs_unit": {
                "code": "0927800",
                "description": "ALF/PORTO DE ITAJAI",
                "state": "SC",
            },
            "cached_country": {
                "code": "2496",
                "name": "ESTADOS UNIDOS",
                "iso_alpha_2": "US",
            },
        },
    )

    assert result["ready_for_draft"] is True
    assert result["normalized"]["clearance_location"] == "ALF/PORTO DE ITAJAI"
    assert result["normalized"]["clearance_state"] == "SC"
    assert result["normalized"]["foreign_supplier"]["country_code"] == "2496"
    assert result["fields"]["clearance_location"]["source"] == (
        "local_fiscal_reference"
    )


def test_context_resolves_hafele_official_references_and_duimp_costs():
    result = NfeContextResolver().resolve(
        normalized={
            "registration_date": "2026-05-27",
            "clearance_location_code": "0917900",
            "clearance_date": "2026-05-27",
            "transport_mode_code": "1",
            "afrmm_value": "0",
            "tax_totals": {
                "taxa_utilizacao": {"value": "285.34"},
            },
            "foreign_supplier": {"country_iso_alpha_2": "DE"},
        },
        external={
            "cached_customs_unit": {
                "code": "0917900",
                "description": "TCP - TERMINAL",
                "state": "PR",
            },
            "cached_country": {
                "code": "0230",
                "name": "ALEMANHA",
                "iso_alpha_2": "DE",
            },
        },
    )

    assert result["ready_for_draft"] is True
    assert result["normalized"]["clearance_location"] == "TCP - TERMINAL"
    assert result["normalized"]["clearance_state"] == "PR"
    assert result["normalized"]["foreign_supplier"]["country_code"] == "0230"
    assert result["normalized"]["foreign_supplier"]["country_name"] == "ALEMANHA"
    assert result["suggested"]["additional_costs"] == {
        "afrmm": "0",
        "siscomex_fee": "285.34",
    }


def test_cargo_identifier_prefers_air_waybill_over_ruc():
    identifier = ImportNfeService._cargo_identifier(
        {
            "raw": {
                "dadosGerais": {
                    "carga": {"identificacao": "6BR-RUC-FALLBACK"},
                    "documentos": {
                        "documentosInstrucao": [
                            {
                                "tipo": {"codigo": "30"},
                                "palavrasChave": [
                                    {"codigo": "1", "valor": "047-35401251/005153"}
                                ],
                            }
                        ]
                    },
                }
            }
        }
    )

    assert identifier == "047-35401251/005153"
