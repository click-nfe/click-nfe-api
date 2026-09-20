from decimal import Decimal

import pytest

from app.services.import_tax_calculator import (
    ImportTaxCalculationError,
    ImportTaxCalculator,
)


def reference_item():
    return {
        "product_value": "6054.39",
        "freight_value": "0",
        "insurance_value": "0",
        "discount_value": "0",
        "other_value": "0",
        "tax_classification_code": "000001",
        "tax_payload": {
            "ii": {"base": "6054.39", "rate": "18", "value": "1089.79"},
            "ipi": {"base": "7144.18", "rate": "3.25", "value": "232.19"},
            "pis": {"base": "6054.39", "rate": "3.12", "value": "188.90"},
            "cofins": {
                "base": "6054.39",
                "rate": "14.37",
                "value": "870.02",
            },
        },
        "import_payload": {},
    }


def configuration():
    return {
        "icms_rate": "12",
        "icms_origin": "1",
        "icms_cst": "90",
        "ipi_cst": "49",
        "pis_cst": "98",
        "cofins_cst": "98",
        "ibs_cbs_cst": "000",
        "tax_classification_code": "000001",
        "ibs_uf_rate": "0.1",
        "ibs_mun_rate": "0",
        "cbs_rate": "0.9",
    }


def test_reproduces_reference_import_tax_bases_for_first_item():
    items, totals = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration=configuration(),
        additional_costs={"afrmm": "53.46", "other": "35.36"},
    )

    item = items[0]
    assert item["other_value"] == "88.82"
    assert item["import_payload"]["afrmm_value"] == "53.46"
    assert item["tax_payload"]["icms"]["base"] == "9686.49"
    assert item["tax_payload"]["icms"]["value"] == "1162.38"
    assert item["tax_payload"]["ibs_cbs"]["base"] == "5011.70"
    assert item["tax_payload"]["ibs_cbs"]["ibs_uf_value"] == "5.01"
    assert item["tax_payload"]["ibs_cbs"]["cbs_value"] == "45.11"
    assert totals["invoice_value"] == "9686.49"
    assert totals["rtc_invoice_value"] == "7465.19"



def test_calculates_taxed_icms00_import():
    taxed_configuration = configuration()
    taxed_configuration["icms_cst"] = "00"

    items, totals = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration=taxed_configuration,
        additional_costs={"afrmm": "53.46", "other": "35.36"},
    )

    icms = items[0]["tax_payload"]["icms"]
    assert icms["cst"] == "00"
    assert icms["base_method"] == "3"
    assert icms["base"] == "9686.49"
    assert icms["rate"] == "12.0000"
    assert icms["value"] == "1162.38"
    assert totals["icms_base"] == "9686.49"
    assert totals["icms_value"] == "1162.38"


def test_calculates_icms10_with_mva_substitution_tax():
    items, totals = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration={
            "icms_origin": "1",
            "icms_cst": "10",
            "icms_rate": "12",
            "icms_st_base_method": "4",
            "icms_st_mva_rate": "40",
            "icms_st_rate": "18",
        },
    )

    icms = items[0]["tax_payload"]["icms"]
    assert icms["base"] == "9585.56"
    assert icms["value"] == "1150.27"
    assert icms["st_base"] == "13419.78"
    assert icms["st_value"] == "1265.29"
    assert icms["diagnostic_only"] is True
    assert totals["icms_st_base"] == "13419.78"
    assert totals["icms_st_value"] == "1265.29"
    assert totals["invoice_value"] == "10850.85"


def test_calculates_icms20_reduced_base_inside_price():
    items, totals = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration={
            "icms_origin": "1",
            "icms_cst": "20",
            "icms_rate": "12",
            "icms_base_reduction_rate": "25",
        },
    )

    icms = items[0]["tax_payload"]["icms"]
    assert icms["base_reduction_rate"] == "25.0000"
    assert icms["base"] == "6952.16"
    assert icms["value"] == "834.26"
    assert totals["icms_base"] == "6952.16"
    assert totals["icms_value"] == "834.26"


def test_calculates_icms30_only_with_substitution_tax():
    items, totals = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration={
            "icms_origin": "1",
            "icms_cst": "30",
            "icms_st_base_method": "4",
            "icms_st_mva_rate": "40",
            "icms_st_rate": "18",
        },
    )

    icms = items[0]["tax_payload"]["icms"]
    assert icms["base"] == "0.00"
    assert icms["value"] == "0.00"
    assert icms["st_base"] == "11809.41"
    assert icms["st_value"] == "2125.69"
    assert totals["icms_value"] == "0.00"
    assert totals["icms_st_value"] == "2125.69"


def test_preserves_previously_retained_values_for_icms60():
    items, totals = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration={
            "icms_origin": "1",
            "icms_cst": "60",
            "icms_st_retained_base": "1000",
            "icms_st_retained_rate": "18",
            "icms_st_retained_value": "180",
        },
    )

    icms = items[0]["tax_payload"]["icms"]
    assert icms["retained_st_base"] == "1000.00"
    assert icms["retained_st_rate"] == "18.0000"
    assert icms["retained_st_value"] == "180.00"
    assert totals["icms_st_value"] == "0.00"
    assert totals["invoice_value"] == "8435.29"


def test_calculates_icms70_reduced_base_and_substitution_tax():
    items, totals = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration={
            "icms_origin": "1",
            "icms_cst": "70",
            "icms_rate": "12",
            "icms_base_reduction_rate": "25",
            "icms_st_base_method": "4",
            "icms_st_mva_rate": "40",
            "icms_st_rate": "18",
        },
    )

    icms = items[0]["tax_payload"]["icms"]
    assert icms["base"] == "6952.16"
    assert icms["value"] == "834.26"
    assert icms["st_base"] == "12977.37"
    assert icms["st_value"] == "1501.67"
    assert totals["icms_st_value"] == "1501.67"
    assert totals["invoice_value"] == "10771.22"


def test_requires_st_parameters_for_icms10():
    with pytest.raises(ImportTaxCalculationError, match="alíquota ST"):
        ImportTaxCalculator().calculate(
            [reference_item()],
            configuration={"icms_cst": "10", "icms_rate": "12"},
        )


def test_requires_retained_values_for_icms60():
    with pytest.raises(ImportTaxCalculationError, match="retido anteriormente"):
        ImportTaxCalculator().calculate(
            [reference_item()],
            configuration={"icms_cst": "60"},
        )

def test_allocates_costs_by_largest_remainder_with_exact_cent_total():
    calculator = ImportTaxCalculator()
    allocations = calculator.allocate(
        Decimal("10.00"), [Decimal("1"), Decimal("1"), Decimal("1")]
    )
    assert allocations == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
    assert sum(allocations) == Decimal("10.00")


def test_allocates_afrmm_by_net_weight_and_other_costs_by_customs_value():
    first = reference_item()
    first.update(
        {
            "product_value": "100.00",
            "customs_value": "100.00",
            "net_weight": "90",
        }
    )
    second = reference_item()
    second.update(
        {
            "product_value": "300.00",
            "customs_value": "300.00",
            "net_weight": "10",
        }
    )

    items, totals = ImportTaxCalculator().calculate(
        [first, second],
        configuration=configuration(),
        additional_costs={
            "afrmm": "100.00",
            "siscomex_fee": "40.00",
            "thc": "20.00",
            "other": "4.00",
        },
    )

    assert items[0]["cost_allocation"] == {
        "afrmm": "90.00",
        "siscomex_fee": "10.00",
        "thc": "5.00",
        "other": "1.00",
    }
    assert items[1]["cost_allocation"] == {
        "afrmm": "10.00",
        "siscomex_fee": "30.00",
        "thc": "15.00",
        "other": "3.00",
    }
    assert totals["afrmm_value"] == "100.00"
    assert totals["siscomex_fee"] == "40.00"
    assert totals["thc_value"] == "20.00"
    assert totals["additional_other_value"] == "4.00"


def test_requires_item_net_weight_to_allocate_afrmm_across_multiple_items():
    with pytest.raises(ImportTaxCalculationError, match="peso líquido"):
        ImportTaxCalculator().calculate(
            [reference_item(), reference_item()],
            configuration=configuration(),
            additional_costs={"afrmm": "1.00"},
        )


def test_rejects_negative_additional_costs():
    with pytest.raises(
        ImportTaxCalculationError, match="não podem ser negativas"
    ):
        ImportTaxCalculator().calculate(
            [reference_item()],
            configuration=configuration(),
            additional_costs={"thc": "-0.01"},
        )


def test_reconciles_official_duimp_taxes_and_allocated_costs():
    calculator = ImportTaxCalculator()
    items, totals = calculator.calculate(
        [reference_item()],
        configuration=configuration(),
        additional_costs={
            "afrmm": "53.46",
            "siscomex_fee": "10.00",
            "thc": "20.00",
            "other": "5.36",
        },
    )

    result = calculator.reconcile(
        items,
        totals,
        expected_tax_totals={
            "ii": {"value": "1089.79"},
            "ipi": {"value": "232.19"},
            "pis": {"value": "188.90"},
            "cofins": {"value": "870.02"},
        },
        expected_additional_costs={
            "afrmm": "53.46",
            "siscomex_fee": "10.00",
            "thc": "20.00",
            "other": "5.36",
        },
    )

    assert result["status"] == "balanced"
    assert result["failed_checks"] == 0
    assert len(result["checks"]) == 8


def test_marks_reconciliation_for_review_when_official_total_diverges():
    calculator = ImportTaxCalculator()
    items, totals = calculator.calculate(
        [reference_item()],
        configuration=configuration(),
    )

    result = calculator.reconcile(
        items,
        totals,
        expected_tax_totals={"ii": {"value": "1090.00"}},
    )

    assert result["status"] == "requires_review"
    assert result["failed_checks"] == 1
    assert result["checks"][0]["difference"] == "-0.21"
    assert result["checks"][0]["blocking"] is True
    assert result["blocking_failed_checks"] == 1
    assert result["warning_checks"] == 0


def test_marks_full_reduction_diagnostic_icms_difference_as_non_blocking():
    calculator = ImportTaxCalculator()
    items, totals = calculator.calculate(
        [reference_item()],
        configuration={
            "icms_origin": "1",
            "icms_cst": "51",
            "icms_base_method": "3",
            "icms_base_reduction_rate": "100",
            "icms_base_allocation": "per_item",
            "icms_benefit_code": "PR839999",
            "ipi_cst": "00",
            "pis_cst": "99",
            "cofins_cst": "99",
        },
    )

    result = calculator.reconcile(
        items,
        totals,
        expected_tax_totals={"icms": {"value": "100.00"}},
    )

    assert result["status"] == "requires_review"
    assert result["failed_checks"] == 1
    assert result["blocking_failed_checks"] == 0
    assert result["warning_checks"] == 1
    assert result["checks"][0]["blocking"] is False
    assert result["checks"][0]["code"] == (
        "diagnostic_icms_reconciliation_difference"
    )


def test_calculates_full_icms_deferment_diagnostic_for_ordemilk_duimp():
    values = [
        ("9590.25", "1918.05", "374.02", "201.40", "983.00"),
        ("9033.55", "1806.71", "352.31", "189.70", "925.94"),
        ("13999.46", "1763.93", "0", "293.99", "1434.94"),
        ("10631.04", "1339.51", "0", "223.25", "1089.68"),
    ]
    items = []
    for product, ii, ipi, pis, cofins in values:
        items.append(
            {
                "product_value": product,
                "customs_value": product,
                "net_weight": "1",
                "freight_value": "0",
                "insurance_value": "0",
                "discount_value": "0",
                "other_value": "0",
                "tax_payload": {
                    "ii": {"base": product, "value": ii},
                    "ipi": {"base": product, "value": ipi},
                    "pis": {"base": product, "value": pis},
                    "cofins": {"base": product, "value": cofins},
                },
                "import_payload": {},
            }
        )

    calculated, totals = ImportTaxCalculator().calculate(
        items,
        configuration={
            "icms_origin": "1",
            "icms_cst": "51",
            "icms_base_method": "3",
            "icms_deferment_rate": "100",
            "ipi_cst": "49",
            "pis_cst": "98",
            "cofins_cst": "98",
        },
        additional_costs={"siscomex_fee": "192.79"},
    )

    assert totals["invoice_value"] == "56343.52"
    assert totals["icms_base"] == "56343.52"
    assert totals["icms_value"] == "0.00"
    assert [item["tax_payload"]["icms"]["base"] for item in calculated] == [
        "12492.36",
        "11767.20",
        "18235.85",
        "13848.11",
    ]
    icms = calculated[0]["tax_payload"]["icms"]
    assert icms["rate"] is None
    assert icms["operation_value"] is None
    assert icms["deferred_value"] is None
    assert icms["deferment_rate"] == "100.0000"
    assert icms["diagnostic_only"] is True


def test_calculates_hafele_full_base_reduction_and_item_ipi_treatment():
    base_item = {
        "product_value": "392.29",
        "customs_value": "549.08",
        "freight_value": "156.33",
        "insurance_value": "0.47",
        "net_weight": "3.768",
        "discount_value": "0",
        "other_value": "0",
        "tax_payload": {
            "ii": {"base": "549.08", "rate": "18", "value": "98.83"},
            "ipi": {"base": "647.9144", "rate": "3.25", "value": "21.06"},
            "pis": {"base": "549.08", "rate": "2.1", "value": "11.53"},
            "cofins": {"base": "549.08", "rate": "9.65", "value": "52.99"},
        },
        "import_payload": {},
    }
    zero_ipi_item = {
        **base_item,
        "product_value": "7954.74",
        "customs_value": "8634.14",
        "freight_value": "669.90",
        "insurance_value": "9.50",
        "tax_payload": {
            **base_item["tax_payload"],
            "ipi": {"base": "10015.6024", "rate": "0", "value": "0"},
        },
    }

    calculated, _ = ImportTaxCalculator().calculate(
        [base_item, zero_ipi_item],
        configuration={
            "icms_origin": "1",
            "icms_cst": "51",
            "icms_base_method": "3",
            "icms_base_reduction_rate": "100",
            "icms_base_allocation": "per_item",
            "icms_benefit_code": "PR839999",
            "ipi_cst": "00",
            "ipi_zero_rate_cst": "01",
            "pis_cst": "99",
            "cofins_cst": "99",
        },
    )

    first_icms = calculated[0]["tax_payload"]["icms"]
    assert first_icms["base"] == "733.50"
    assert first_icms["base_reduction_rate"] == "100.0000"
    assert first_icms["value"] is None
    assert calculated[0]["benefit_code"] == "PR839999"
    assert calculated[0]["tax_payload"]["ipi"]["cst"] == "00"
    assert calculated[1]["tax_payload"]["ipi"]["cst"] == "01"


@pytest.mark.parametrize("cst", ["40", "41", "50"])
def test_calculates_non_taxed_icms_without_nominal_rate(cst):
    calculated, totals = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration={
            "icms_origin": "1",
            "icms_cst": cst,
            "icms_tax_treatment_confirmed": False,
            "ipi_cst": "49",
            "pis_cst": "98",
            "cofins_cst": "98",
        },
        additional_costs={"siscomex_fee": "192.79"},
    )

    icms = calculated[0]["tax_payload"]["icms"]
    assert icms["cst"] == cst
    assert icms["base"] == "0.00"
    assert icms["reference_base"] == "8628.08"
    assert icms["rate"] is None
    assert icms["value"] == "0.00"
    assert icms["tax_treatment_confirmed"] is False
    assert icms["diagnostic_only"] is True
    assert totals["icms_base"] == "0.00"
    assert totals["icms_value"] == "0.00"


def test_marks_non_taxed_icms_as_fiscal_after_explicit_confirmation():
    calculated, _ = ImportTaxCalculator().calculate(
        [reference_item()],
        configuration={
            "icms_origin": "1",
            "icms_cst": "40",
            "icms_tax_treatment_confirmed": True,
        },
    )

    icms = calculated[0]["tax_payload"]["icms"]
    assert icms["tax_treatment_confirmed"] is True
    assert icms["diagnostic_only"] is False


def test_rejects_nominal_rate_for_non_taxed_icms():
    with pytest.raises(
        ImportTaxCalculationError,
        match="CST 40 não aceita alíquota nominal",
    ):
        ImportTaxCalculator().calculate(
            [reference_item()],
            configuration={"icms_cst": "40", "icms_rate": "19.5"},
        )
