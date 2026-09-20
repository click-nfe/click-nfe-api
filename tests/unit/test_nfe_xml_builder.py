from xml.etree import ElementTree as ET

import pytest

from app.services.nfe_xml_builder import NfeXmlBuilder
from app.services.nfe_xsd_validator import NfeXsdValidator


NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


def payload():
    return {
        "document": {
            "state_code": "41",
            "cnf": "76336237",
            "operation_nature": "Importação",
            "series": "1",
            "number": 14422,
            "issue_datetime": "2026-07-16T11:18:38-03:00",
            "tp_emis": "1",
            "check_digit": "5",
            "environment": "production",
        },
        "issuer": {
            "cnpj": "00000000000191",
            "legal_name": "IMPORTADORA TESTE LTDA",
            "state_registration": "1234567890",
            "tax_regime": "3",
            "address": {
                "street": "Rua de Teste",
                "number": "100",
                "district": "Centro",
                "city_code": "4106902",
                "city_name": "Curitiba",
                "state": "PR",
                "zip_code": "80000000",
                "country_code": "1058",
                "country_name": "Brasil",
            },
        },
        "recipient": {
            "party_type": "foreign",
            "foreign_id": "",
            "legal_name": "FOREIGN SUPPLIER LTD",
            "address": {
                "street": "EXTERIOR",
                "number": "0",
                "district": "EXTERIOR",
                "city_code": "9999999",
                "city_name": "EXTERIOR",
                "state": "EX",
                "country_code": "1600",
                "country_name": "CHINA",
            },
        },
        "duimp": {
            "number": "26BR0000000000-1",
            "api_number": "26BR00000000001",
            "registration_date": "2026-07-14",
            "clearance_location": "0917800 - PORTO DE PARANAGUA",
            "clearance_state": "PR",
            "clearance_date": "2026-07-15",
            "transport_mode_code": "1",
            "intermediation_type": "1",
        },
        "items": [
            {
                "item_number": 1,
                "product_code": "PROD-001",
                "description": "Produto sanitizado para teste",
                "ncm": "87087090",
                "cfop": "3102",
                "commercial_unit": "PECAS",
                "commercial_quantity": "12",
                "commercial_unit_value": "504.5325",
                "taxable_unit": "PECAS",
                "taxable_quantity": "12",
                "taxable_unit_value": "504.5325",
                "product_value": "6054.39",
                "freight_value": "0",
                "insurance_value": "0",
                "discount_value": "0",
                "other_value": "88.82",
                "import_payload": {
                    "afrmm_value": "53.46",
                    "sequence_number": "1",
                    "manufacturer_code": "0000",
                    "exporter_code": "EXP-TESTE-001",
                },
                "tax_payload": {
                    "icms": {
                        "origin": "1", "cst": "90", "base_method": "3",
                        "base": "9686.49", "rate": "12", "value": "1162.38",
                    },
                    "ipi": {"cst": "49", "base": "7144.18", "rate": "3.25", "value": "232.19"},
                    "ii": {"base": "6054.39", "customs_expenses": "0", "value": "1089.79", "iof": "0"},
                    "pis": {"cst": "98", "base": "6054.39", "rate": "3.12", "value": "188.90"},
                    "cofins": {"cst": "98", "base": "6054.39", "rate": "14.37", "value": "870.02"},
                    "ibs_cbs": {
                        "cst": "000", "classification": "000001", "base": "5011.70",
                        "ibs_uf_rate": "0.1", "ibs_uf_value": "5.01",
                        "ibs_mun_rate": "0", "ibs_mun_value": "0", "ibs_value": "5.01",
                        "cbs_rate": "0.9", "cbs_value": "45.11",
                    },
                },
            }
        ],
        "totals": {
            "icms_base": "9686.49", "icms_value": "1162.38", "products_value": "6054.39",
            "freight_value": "0", "insurance_value": "0", "discount_value": "0",
            "ii_value": "1089.79", "ipi_value": "232.19", "pis_value": "188.90",
            "cofins_value": "870.02", "other_value": "88.82", "invoice_value": "9686.49",
            "ibs_cbs_base": "5011.70", "ibs_uf_value": "5.01", "ibs_mun_value": "0",
            "ibs_value": "5.01", "cbs_value": "45.11", "rtc_invoice_value": "7465.19",
        },
        "transport": {"freight_mode": "9"},
        "payment": {"method": "90", "value": "0"},
        "additional_info": {"complementary": "DUIMP de teste sanitizada."},
    }


def test_builds_unsigned_import_nfe_with_foreign_recipient_and_duimp():
    xml = NfeXmlBuilder().build(
        payload(), access_key="41260700000000000191550010000144221763362375"
    )
    root = ET.fromstring(xml)

    assert root.tag == "{http://www.portalfiscal.inf.br/nfe}NFe"
    inf_nfe = root.find("nfe:infNFe", NS)
    assert inf_nfe.attrib["Id"].startswith("NFe41")
    assert root.findtext(".//nfe:dest/nfe:enderDest/nfe:UF", namespaces=NS) == "EX"
    assert root.findtext(".//nfe:DI/nfe:nDI", namespaces=NS) == "26BR00000000001"
    assert root.findtext(".//nfe:ICMS90/nfe:vICMS", namespaces=NS) == "1162.38"
    assert root.findtext(".//nfe:IBSCBS/nfe:cClassTrib", namespaces=NS) == "000001"
    assert root.findtext(".//nfe:total/nfe:vNFTot", namespaces=NS) == "7465.19"
    assert root.find(".//{http://www.w3.org/2000/09/xmldsig#}Signature") is None



def test_builds_xsd_valid_icms00_group():
    data = payload()
    data["items"][0]["tax_payload"]["icms"] = {
        "origin": "1",
        "cst": "00",
        "base_method": "3",
        "base": "9686.49",
        "rate": "12",
        "value": "1162.38",
    }

    xml = NfeXmlBuilder().build(
        data,
        access_key="41260700000000000191550010000144221763362375",
    )
    root = ET.fromstring(xml)
    icms00 = root.find(".//nfe:ICMS00", NS)

    assert icms00 is not None
    assert icms00.findtext("nfe:orig", namespaces=NS) == "1"
    assert icms00.findtext("nfe:CST", namespaces=NS) == "00"
    assert icms00.findtext("nfe:modBC", namespaces=NS) == "3"
    assert icms00.findtext("nfe:vBC", namespaces=NS) == "9686.49"
    assert icms00.findtext("nfe:pICMS", namespaces=NS) == "12.0000"
    assert icms00.findtext("nfe:vICMS", namespaces=NS) == "1162.38"
    assert root.find(".//nfe:ICMS90", NS) is None
    assert NfeXsdValidator().validate(xml, allow_unsigned=True).is_valid is True


@pytest.mark.parametrize(
    ("cst", "icms_data", "expected"),
    [
        (
            "10",
            {
                "base_method": "3",
                "base": "9585.56",
                "rate": "12",
                "value": "1150.27",
                "st_base_method": "4",
                "st_mva_rate": "40",
                "st_base": "13419.78",
                "st_rate": "18",
                "st_value": "1265.29",
            },
            {"vBC": "9585.56", "pMVAST": "40.0000", "vICMSST": "1265.29"},
        ),
        (
            "20",
            {
                "base_method": "3",
                "base_reduction_rate": "25",
                "base": "6952.16",
                "rate": "12",
                "value": "834.26",
            },
            {"pRedBC": "25.0000", "vBC": "6952.16", "vICMS": "834.26"},
        ),
        (
            "30",
            {
                "st_base_method": "6",
                "st_base": "8435.29",
                "st_rate": "18",
                "st_value": "1518.35",
            },
            {"modBCST": "6", "vBCST": "8435.29", "vICMSST": "1518.35"},
        ),
        (
            "60",
            {
                "retained_st_base": "1000",
                "retained_st_rate": "18",
                "retained_st_value": "180",
            },
            {"vBCSTRet": "1000.00", "pST": "18.0000", "vICMSSTRet": "180.00"},
        ),
        (
            "70",
            {
                "base_method": "3",
                "base_reduction_rate": "25",
                "base": "6952.16",
                "rate": "12",
                "value": "834.26",
                "st_base_method": "4",
                "st_mva_rate": "40",
                "st_base": "12977.37",
                "st_rate": "18",
                "st_value": "1501.67",
            },
            {"pRedBC": "25.0000", "vBCST": "12977.37", "vICMSST": "1501.67"},
        ),
    ],
)
def test_builds_xsd_valid_additional_icms_groups(cst, icms_data, expected):
    data = payload()
    data["items"][0]["tax_payload"]["icms"] = {
        "origin": "1",
        "cst": cst,
        **icms_data,
    }
    data["totals"]["icms_base"] = icms_data.get("base", "0")
    data["totals"]["icms_value"] = icms_data.get("value", "0")
    data["totals"]["icms_st_base"] = icms_data.get("st_base", "0")
    data["totals"]["icms_st_value"] = icms_data.get("st_value", "0")

    xml = NfeXmlBuilder().build(
        data,
        access_key="41260700000000000191550010000144221763362375",
    )
    root = ET.fromstring(xml)
    group = root.find(f".//nfe:ICMS{cst}", NS)

    assert group is not None
    for tag, value in expected.items():
        assert group.findtext(f"nfe:{tag}", namespaces=NS) == value
    assert NfeXsdValidator().validate(xml, allow_unsigned=True).is_valid is True

def test_normalizes_nfe_datetime_to_seconds():
    data = payload()
    data["document"]["issue_datetime"] = "2026-07-16T11:18:38.123456-03:00"

    xml = NfeXmlBuilder().build(
        data,
        access_key="41260700000000000191550010000144221763362375",
    )
    root = ET.fromstring(xml)

    assert (
        root.findtext(".//nfe:ide/nfe:dhEmi", namespaces=NS)
        == "2026-07-16T11:18:38-03:00"
    )


def test_builds_operation_indicators_carrier_and_volume():
    data = payload()
    data["document"].update(
        {
            "presence_indicator": "9",
            "intermediary_indicator": "0",
        }
    )
    data["transport"] = {
        "freight_mode": "1",
        "carrier": {
            "tax_id": "06.255.344/0001-28",
            "name": "S2 LOG",
            "state_registration": "9086113225",
            "address": "RUA VEREADOR ANGELO BURBELLO 800",
            "city_name": "CURITIBA",
            "state": "PR",
        },
        "volume": {
            "quantity": 8,
            "species": "DIVERSOS",
            "net_weight": "46.88800",
            "gross_weight": "51.50000",
        },
    }

    xml = NfeXmlBuilder().build(
        data,
        access_key="41260700000000000191550010000144221763362375",
    )
    root = ET.fromstring(xml)

    assert root.findtext(".//nfe:ide/nfe:indPres", namespaces=NS) == "9"
    assert root.findtext(".//nfe:ide/nfe:indIntermed", namespaces=NS) == "0"
    assert root.findtext(".//nfe:transporta/nfe:CNPJ", namespaces=NS) == (
        "06255344000128"
    )
    assert root.findtext(".//nfe:transporta/nfe:xNome", namespaces=NS) == (
        "S2 LOG"
    )
    assert root.findtext(".//nfe:vol/nfe:pesoL", namespaces=NS) == "46.888"
    assert root.findtext(".//nfe:vol/nfe:pesoB", namespaces=NS) == "51.500"
    assert NfeXsdValidator().validate(xml, allow_unsigned=True).is_valid is True


def test_unsigned_xml_passes_official_xsd_with_validation_signature():
    xml = NfeXmlBuilder().build(
        payload(),
        access_key="41260700000000000191550010000144221763362375",
    )

    result = NfeXsdValidator().validate(xml, allow_unsigned=True)

    assert result.is_valid is True
    assert result.errors == []
    assert result.schema_package == "PL_010e_v1.02"
    assert "<Signature" not in xml


def test_xsd_validator_reports_invalid_issue_datetime():
    xml = NfeXmlBuilder().build(
        payload(),
        access_key="41260700000000000191550010000144221763362375",
    )
    xml = xml.replace(
        "2026-07-16T11:18:38-03:00",
        "2026-07-16T11:18:38.123456-03:00",
    )

    result = NfeXsdValidator().validate(xml, allow_unsigned=True)

    assert result.is_valid is False
    assert any("dhEmi" in error["message"] for error in result.errors)


def test_builds_xsd_valid_diagnostic_icms51_without_nominal_values():
    data = payload()
    data["items"][0]["import_payload"]["addition_number"] = "2"
    data["items"][0]["tax_payload"]["icms"] = {
        "origin": "1",
        "cst": "51",
        "base_method": "3",
        "base": "9686.49",
        "rate": None,
        "operation_value": None,
        "deferment_rate": "100",
        "deferred_value": None,
        "value": "0",
        "diagnostic_only": True,
    }
    data["totals"]["icms_value"] = "0"

    xml = NfeXmlBuilder().build(
        data,
        access_key="41260700000000000191550010000144221763362375",
    )
    root = ET.fromstring(xml)
    icms51 = root.find(".//nfe:ICMS51", NS)

    assert icms51 is not None
    assert icms51.findtext("nfe:vBC", namespaces=NS) == "9686.49"
    assert icms51.findtext("nfe:pDif", namespaces=NS) == "100.0000"
    assert icms51.findtext("nfe:vICMS", namespaces=NS) == "0.00"
    assert icms51.find("nfe:pICMS", NS) is None
    assert icms51.find("nfe:vICMSOp", NS) is None
    assert icms51.find("nfe:vICMSDif", NS) is None
    assert root.findtext(".//nfe:adi/nfe:nAdicao", namespaces=NS) == "2"
    assert NfeXsdValidator().validate(xml, allow_unsigned=True).is_valid is True


def test_builds_xsd_valid_icms51_base_reduction_benefit_and_ipint():
    data = payload()
    data["items"][0]["benefit_code"] = "PR839999"
    data["items"][0]["tax_payload"]["icms"] = {
        "origin": "1",
        "cst": "51",
        "base_method": "3",
        "base_reduction_rate": "100",
        "base": "9686.49",
        "rate": None,
        "value": None,
        "diagnostic_only": True,
    }
    data["items"][0]["tax_payload"]["ipi"] = {
        "cst": "01",
        "enquiry_code": "999",
        "base": "7144.18",
        "rate": "0",
        "value": "0",
    }
    data["totals"]["icms_value"] = "0"
    data["totals"]["ipi_value"] = "0"

    xml = NfeXmlBuilder().build(
        data,
        access_key="41260700000000000191550010000144221763362375",
    )
    root = ET.fromstring(xml)
    icms51 = root.find(".//nfe:ICMS51", NS)

    assert root.findtext(".//nfe:prod/nfe:cBenef", namespaces=NS) == "PR839999"
    assert icms51.findtext("nfe:pRedBC", namespaces=NS) == "100.0000"
    assert icms51.find("nfe:pICMS", NS) is None
    assert icms51.find("nfe:vICMS", NS) is None
    assert root.findtext(".//nfe:IPI/nfe:IPINT/nfe:CST", namespaces=NS) == "01"
    assert root.find(".//nfe:IPI/nfe:IPITrib", NS) is None
    assert NfeXsdValidator().validate(xml, allow_unsigned=True).is_valid is True


def test_builds_xsd_valid_diagnostic_icms40_without_nominal_values():
    data = payload()
    data["items"][0]["tax_payload"]["icms"] = {
        "origin": "1",
        "cst": "40",
        "base": "0",
        "rate": None,
        "value": "0",
        "tax_treatment_confirmed": False,
        "diagnostic_only": True,
    }
    data["totals"]["icms_base"] = "0"
    data["totals"]["icms_value"] = "0"

    xml = NfeXmlBuilder().build(
        data,
        access_key="41260700000000000191550010000144221763362375",
    )
    root = ET.fromstring(xml)
    icms40 = root.find(".//nfe:ICMS40", NS)

    assert icms40 is not None
    assert icms40.findtext("nfe:orig", namespaces=NS) == "1"
    assert icms40.findtext("nfe:CST", namespaces=NS) == "40"
    assert icms40.find("nfe:vICMSDeson", NS) is None
    assert icms40.find("nfe:motDesICMS", NS) is None
    assert NfeXsdValidator().validate(xml, allow_unsigned=True).is_valid is True
