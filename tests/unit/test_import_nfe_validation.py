from types import SimpleNamespace

from app.services.import_process import ImportNfeService


def test_validation_rejects_item_not_enriched_by_catalog():
    service = ImportNfeService(
        current_user=SimpleNamespace(organization_id=None, id=None)
    )

    result = service.validate_nfe_payload(
        {
            "document": {
                "model": "55",
                "operation_type": "entry",
                "environment": "homologation",
                "series": "1",
            },
            "duimp": {
                "number": "26BR0000000000-1",
                "registration_date": "2026-07-14",
                "clearance_location": "PARANAGUA",
                "clearance_state": "PR",
                "clearance_date": "2026-07-15",
                "transport_mode_code": "1",
            },
            "items": [
                {
                    "description": "Mercadoria importada",
                    "ncm": "87087090",
                    "cfop": "3102",
                    "commercial_quantity": "1",
                    "product_value": "100",
                    "import_payload": {"duimp_item_number": "1"},
                    "tax_payload": {
                        tax: {"value": "1"}
                        for tax in ("icms", "ipi", "ii", "pis", "cofins")
                    },
                }
            ],
        }
    )

    assert {
        "field": "items[1].description",
        "message": "Produto não enriquecido pelo Catálogo de Produtos.",
    } in result.errors


def test_validation_rejects_transport_mode_outside_nfe_schema():
    service = ImportNfeService(
        current_user=SimpleNamespace(organization_id=None, id=None)
    )

    result = service.validate_nfe_payload(
        {
            "document": {
                "model": "55",
                "operation_type": "entry",
                "environment": "production",
                "series": "1",
            },
            "duimp": {
                "number": "26BR0000000000-1",
                "registration_date": "2026-07-14",
                "clearance_location": "PARANAGUA",
                "clearance_state": "PR",
                "clearance_date": "2026-07-15",
                "transport_mode_code": "14",
            },
            "items": [],
        }
    )

    assert {
        "field": "duimp.transport_mode_code",
        "message": "Via de transporte inválida. Selecione um código de 1 a 13.",
    } in result.errors


def test_validation_warns_and_blocks_diagnostic_icms_authorization():
    service = ImportNfeService(
        current_user=SimpleNamespace(organization_id=None, id=None)
    )
    authorization = service._authorization_metadata(
        [
            {
                "tax_payload": {
                    "icms": {
                        "cst": "51",
                        "diagnostic_only": True,
                    }
                }
            }
        ]
    )

    assert authorization["ready"] is False
    assert authorization["mode"] == "diagnostic"
    assert authorization["blockers"][0]["code"] == (
        "missing_nominal_icms_rate"
    )


def test_validation_warns_instead_of_rejecting_legacy_diagnostic_icms_check():
    service = ImportNfeService(
        current_user=SimpleNamespace(organization_id=None, id=None)
    )
    item = {
        "product_code": "342.79.752",
        "description": "DOBRADICA PARA MOVEIS",
        "ncm": "83021000",
        "cfop": "3102",
        "commercial_quantity": "1",
        "product_value": "100",
        "import_payload": {"duimp_item_number": "2"},
        "tax_payload": {
            "icms": {
                "cst": "51",
                "base_reduction_rate": "100.0000",
                "rate": None,
                "value": None,
                "diagnostic_only": True,
            },
            **{
                tax: {"value": "1"}
                for tax in ("ipi", "ii", "pis", "cofins")
            },
        },
    }
    payload = {
        "document": {
            "model": "55",
            "operation_type": "entry",
            "environment": "homologation",
            "series": "1",
        },
        "duimp": {
            "number": "26BR0000704908-1",
            "registration_date": "2026-05-27",
            "clearance_location": "TCP - TERMINAL",
            "clearance_state": "PR",
            "clearance_date": "2026-05-27",
            "transport_mode_code": "1",
        },
        "issuer": {
            "cnpj": "02473058000188",
            "legal_name": "Hafele Brasil Ltda.",
            "tax_regime": "3",
            "address": {
                "street": "Rodovia João Leopoldo Jacomel",
                "number": "4459",
                "district": "Jardim Primavera",
                "city_code": "4119509",
                "city_name": "Piraquara",
                "state": "PR",
                "zip_code": "83302000",
            },
        },
        "recipient": {
            "party_type": "foreign",
            "legal_name": "HAFELE SE & CO KG",
            "address": {
                "street": "ADOLF HAFELE STRASSE",
                "number": "1",
                "district": "EXTERIOR",
                "city_code": "9999999",
                "city_name": "NAGOLD",
                "state": "EX",
                "country_code": "0230",
                "country_name": "ALEMANHA",
            },
        },
        "items": [item],
        "authorization": {
            "ready": False,
            "mode": "diagnostic",
            "blockers": [
                {
                    "code": "missing_nominal_icms_rate",
                    "field": "tax_configuration.icms_rate",
                    "message": "Revisão fiscal pendente.",
                }
            ],
        },
        # Legacy drafts do not yet have the `blocking` classification.
        "reconciliation": {
            "status": "requires_review",
            "checks": [
                {
                    "name": "duimp_icms",
                    "expected": "5475.05",
                    "calculated": "0.00",
                    "difference": "-5475.05",
                    "matches": False,
                }
            ],
        },
    }

    result = service.validate_nfe_payload(payload)

    assert result.is_valid is True
    assert not result.errors
    assert any(
        warning.get("code")
        == "diagnostic_icms_reconciliation_difference"
        for warning in result.warnings
    )


def test_validation_blocks_unconfirmed_non_taxed_icms_treatment():
    service = ImportNfeService(
        current_user=SimpleNamespace(organization_id=None, id=None)
    )

    authorization = service._authorization_metadata(
        [
            {
                "tax_payload": {
                    "icms": {
                        "cst": "40",
                        "diagnostic_only": True,
                        "tax_treatment_confirmed": False,
                    }
                }
            }
        ]
    )

    assert authorization["ready"] is False
    assert authorization["mode"] == "diagnostic"
    assert authorization["blockers"] == [
        {
            "code": "unconfirmed_icms_tax_treatment",
            "field": "tax_configuration.icms_cst",
            "message": (
                "A assinatura e a transmissão estão bloqueadas até a equipe "
                "fiscal confirmar se a exoneração integral deve usar ICMS CST "
                "40, 41 ou 50."
            ),
        }
    ]


def test_validation_accepts_confirmed_non_taxed_icms_treatment():
    service = ImportNfeService(
        current_user=SimpleNamespace(organization_id=None, id=None)
    )

    authorization = service._authorization_metadata(
        [
            {
                "tax_payload": {
                    "icms": {
                        "cst": "40",
                        "diagnostic_only": False,
                        "tax_treatment_confirmed": True,
                    }
                }
            }
        ]
    )

    assert authorization == {
        "ready": True,
        "blockers": [],
        "mode": "fiscal",
    }


def test_validation_blocks_additional_csts_until_fiscal_homologation():
    service = ImportNfeService(
        current_user=SimpleNamespace(organization_id=None, id=None)
    )

    authorization = service._authorization_metadata(
        [
            {
                "tax_payload": {
                    "icms": {"cst": cst, "diagnostic_only": True}
                }
            }
            for cst in ("10", "20", "30", "60", "70")
        ]
    )

    assert authorization["ready"] is False
    assert authorization["mode"] == "diagnostic"
    assert authorization["blockers"] == [
        {
            "code": "unconfirmed_icms_cst_calculation",
            "field": "tax_configuration.icms_tax_treatment_confirmed",
            "message": (
                "A assinatura e a transmissão estão bloqueadas até a equipe "
                "fiscal homologar o cálculo dos CSTs de ICMS: 10, 20, 30, 60, "
                "70."
            ),
        }
    ]


def test_tax_configuration_round_trip_preserves_st_parameters():
    item = SimpleNamespace(
        tax_payload={
            "icms": {
                "origin": "1",
                "cst": "70",
                "base_method": "3",
                "rate": "12.0000",
                "base_reduction_rate": "25.0000",
                "st_base_method": "4",
                "st_mva_rate": "40.0000",
                "st_base_reduction_rate": "10.0000",
                "st_rate": "18.0000",
                "retained_st_base": None,
                "retained_st_rate": None,
                "retained_st_value": None,
                "tax_treatment_confirmed": False,
            },
            "ipi": {"cst": "49", "enquiry_code": "999"},
            "pis": {"cst": "98"},
            "cofins": {"cst": "98"},
        }
    )

    configuration = ImportNfeService._tax_configuration_from_item(item)

    assert configuration["icms_st_base_method"] == "4"
    assert configuration["icms_st_mva_rate"] == "40.0000"
    assert configuration["icms_st_base_reduction_rate"] == "10.0000"
    assert configuration["icms_st_rate"] == "18.0000"
