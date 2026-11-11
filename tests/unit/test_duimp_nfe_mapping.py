from app.models import ImportPurpose
from app.services.import_process import ImportNfeService


def test_portal_items_use_product_column_as_editable_cprod():
    service = object.__new__(ImportNfeService)
    source_item = {
        "product_code": "Gray Pipe Gray",
        "catalog_product_code": "Gray Pipe Gray",
        "catalog_product_id": "204",
        "description": "Tubulação",
        "ncm": "39172290",
        "commercial_unit": "UN",
        "quantity": "1",
        "unit_value": "10",
        "product_value": "10",
        "taxable_unit": "UN",
        "taxable_quantity": "1",
        "taxable_unit_value": "10",
        "addition_number": "1",
        "sequence_number": "1",
    }
    duimp = {
        "number": "26BR0000000000-1",
        "raw": {"provider": "portal_unico"},
        "items": [
            {**source_item, "number": "5"},
            {**source_item, "number": "8"},
        ],
    }

    items = service.map_duimp_items_to_nfe_items(
        duimp=duimp,
        import_purpose=ImportPurpose.RESALE.value,
    )

    assert [item["item_number"] for item in items] == [1, 2]
    assert [item["product_code"] for item in items] == ["204", "204"]
    assert [item["import_payload"]["sequence_number"] for item in items] == ["5", "8"]
    assert all(item["import_payload"]["addition_number"] is None for item in items)
    assert all(
        item["import_payload"]["catalog_product_code"] == "204"
        for item in items
    )
    assert all(
        item["import_payload"]["catalog_internal_code"] == "Gray Pipe Gray"
        for item in items
    )
    assert all(
        item["import_payload"]["product_code_source"] == "portal_product_code"
        for item in items
    )


def test_portal_item_without_product_column_keeps_editable_sequence_fallback():
    service = object.__new__(ImportNfeService)
    duimp = {
        "number": "26BR0000000000-1",
        "raw": {"provider": "portal_unico"},
        "items": [{
            "number": "1", "product_code": "", "catalog_product_code": "Gray Pipe Gray",
            "description": "Tubulação", "ncm": "39172290", "commercial_unit": "UN",
            "quantity": "1", "unit_value": "10", "product_value": "10",
            "taxable_unit": "UN", "taxable_quantity": "1", "taxable_unit_value": "10",
        }],
    }
    item = service.map_duimp_items_to_nfe_items(
        duimp=duimp, import_purpose=ImportPurpose.RESALE.value,
    )[0]
    assert item["product_code"] == "00001"
    assert item["import_payload"]["product_code_source"] == "generated_sequence"
    assert item["import_payload"]["catalog_internal_code"] == "Gray Pipe Gray"


def test_part_number_is_preserved_in_draft_without_changing_cprod():
    service = object.__new__(ImportNfeService)
    duimp = {
        "number": "26BR0000000000-1",
        "raw": {"provider": "portal_unico"},
        "items": [{
            "number": "1", "catalog_product_id": "33", "product_code": "33",
            "catalog_internal_code": "13560", "part_numbers": ["13560"],
            "description": "SAKE", "ncm": "22060090", "commercial_unit": "UN",
            "quantity": "1", "unit_value": "10", "product_value": "10",
            "taxable_unit": "UN", "taxable_quantity": "1", "taxable_unit_value": "10",
        }],
    }

    item = service.map_duimp_items_to_nfe_items(
        duimp=duimp, import_purpose=ImportPurpose.RESALE.value,
    )[0]

    assert item["product_code"] == "33"
    assert item["import_payload"]["part_number"] == "13560"
    assert item["import_payload"]["part_numbers"] == ["13560"]
