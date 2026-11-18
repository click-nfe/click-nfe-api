from app.services.import_process import ImportNfeService


def test_document_exporter_inherits_resolved_country_only_for_same_exporter():
    service = object.__new__(ImportNfeService)
    duimp = {
        "foreign_supplier": {
            "code": "EXP-A", "name": "Alpha", "country_code": "2496",
            "country_name": "ESTADOS UNIDOS", "country_iso_alpha_2": "US",
        },
        "foreign_suppliers": [
            {"code": "EXP-A", "name": "Alpha"},
            {"code": "EXP-B", "name": "Beta", "country_code": "1600", "country_name": "CHINA"},
        ],
    }

    _, _, alpha = service._document_exporter(
        {"exporter_code": "EXP-A", "exporter": {"code": "EXP-A", "name": "Alpha"}}, duimp
    )
    _, _, beta = service._document_exporter(
        {"exporter_code": "EXP-B", "exporter": {"code": "EXP-B", "name": "Beta"}}, duimp
    )

    assert alpha["country_code"] == "2496"
    assert alpha["country_name"] == "ESTADOS UNIDOS"
    assert alpha["country_iso_alpha_2"] == "US"
    assert beta["country_code"] == "1600"
    assert beta["country_name"] == "CHINA"


def test_document_exporter_does_not_copy_country_from_another_exporter():
    service = object.__new__(ImportNfeService)
    _, _, supplier = service._document_exporter(
        {"exporter_code": "EXP-B", "exporter": {"code": "EXP-B", "name": "Beta"}},
        {"foreign_supplier": {"code": "EXP-A", "name": "Alpha", "country_code": "2496"}},
    )
    assert "country_code" not in supplier
