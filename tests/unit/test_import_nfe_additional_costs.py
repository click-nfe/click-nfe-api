from types import SimpleNamespace

from app.services.import_process import ImportNfeService


def service():
    return ImportNfeService(
        current_user=SimpleNamespace(organization_id=None, id=None)
    )


def test_resolves_available_costs_from_normalized_source():
    result = service().resolve_additional_costs(
        duimp={
            "afrmm_value": "100.00",
            "tax_totals": {
                "taxa_utilizacao": {"value": "10.00"},
            },
        },
        additional_costs={"thc": "50.00", "other": "0.00"},
    )

    assert result == {
        "afrmm": "100.00",
        "tum": "0",
        "siscomex_fee": "10.00",
        "thc": "50.00",
        "other": "0.00",
    }


def test_explicit_costs_override_normalized_defaults():
    result = service().resolve_additional_costs(
        duimp={
            "afrmm_value": "100.00",
            "tax_totals": {
                "taxa_utilizacao": {"value": "10.00"},
            },
        },
        additional_costs={
            "afrmm": "0.00",
            "siscomex_fee": "0.00",
            "thc": "20.00",
        },
    )

    assert result["afrmm"] == "0.00"
    assert result["siscomex_fee"] == "0.00"
    assert result["thc"] == "20.00"
    assert result["other"] == "0"
