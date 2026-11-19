from decimal import Decimal

from app.services.import_process import ImportNfeService
from app.services.import_tax_calculator import ImportTaxCalculator


def test_cif_changes_xml_values_without_changing_tax_bases_or_invoice_total():
    calculator = ImportTaxCalculator()
    source = {
        "product_value": "741.33",
        "freight_value": "1558.44",
        "insurance_value": "89.34",
        "commercial_quantity": "4",
        "taxable_quantity": "4",
        "commercial_unit_value": "185.3325",
        "taxable_unit_value": "185.3325",
        "customs_value": "2389.11",
        "import_payload": {},
        "tax_payload": {
            "ii": {"base": "2389.11", "value": "0"},
            "pis": {"base": "2389.11", "value": "0"},
            "cofins": {"base": "2389.11", "value": "0"},
        },
    }
    separated, separated_totals = calculator.calculate(
        [source],
        configuration={"icms_cst": "90", "icms_rate": "12"},
    )
    service = object.__new__(ImportNfeService)
    service.tax_calculator = calculator
    included = service._include_international_costs_in_product(separated)
    totals = service.calculate_nfe_totals(included)
    item = included[0]

    assert item["product_value"] == "2389.11"
    assert item["freight_value"] == item["insurance_value"] == "0.00"
    assert item["commercial_unit_value"] == item["taxable_unit_value"] == "597.2775000000"
    assert Decimal(item["commercial_unit_value"]) * 4 == Decimal(item["product_value"])
    assert item["import_payload"]["international_costs_in_product"] == {
        "original_product_value": "741.33",
        "freight_value": "1558.44",
        "insurance_value": "89.34",
    }
    assert item["tax_payload"] == separated[0]["tax_payload"]
    assert totals["invoice_value"] == separated_totals["invoice_value"]
    assert totals["products_value"] == "2389.11"
    assert totals["freight_value"] == totals["insurance_value"] == "0.00"
