import pytest
from marshmallow import ValidationError

from app.schemas.nfe_automation import ClientImportTaxRuleSchema
from app.schemas.import_process import (
    CreateImportProcessSchema,
    CreateNfeDraftFromDuimpSchema,
    FetchDuimpSchema,
    NfeWorkflowStateQuerySchema,
)


def diagnostic_icms51_rule():
    return {
        "name": "SC industrializacao diferimento integral",
        "issuer_state": "SC",
        "import_purpose": "industrialization",
        "configuration_json": {
            "cfop": "3101",
            "icms_origin": "1",
            "icms_cst": "51",
            "icms_deferment_rate": "100",
        },
    }


def test_accepts_diagnostic_icms51_without_nominal_rate():
    result = ClientImportTaxRuleSchema().load(diagnostic_icms51_rule())

    assert result["configuration_json"]["icms_cst"] == "51"
    assert "icms_rate" not in result["configuration_json"]



def test_accepts_icms00_with_nominal_rate():
    payload = {
        "name": "SP importação própria tributada",
        "issuer_state": "SP",
        "import_purpose": "resale",
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": "00",
            "icms_rate": "12",
        },
    }

    result = ClientImportTaxRuleSchema().load(payload)

    assert result["configuration_json"]["icms_cst"] == "00"
    assert result["configuration_json"]["icms_rate"] == "12"

def test_rejects_partial_deferment_without_nominal_rate():
    payload = diagnostic_icms51_rule()
    payload["configuration_json"]["icms_deferment_rate"] = "50"

    with pytest.raises(ValidationError, match="diferimento ou redução"):
        ClientImportTaxRuleSchema().load(payload)


def test_accepts_diagnostic_icms51_with_full_base_reduction():
    payload = diagnostic_icms51_rule()
    payload["configuration_json"].pop("icms_deferment_rate")
    payload["configuration_json"].update(
        {
            "icms_base_reduction_rate": "100",
            "icms_benefit_code": "PR839999",
        }
    )

    result = ClientImportTaxRuleSchema().load(payload)

    assert result["configuration_json"]["icms_base_reduction_rate"] == "100"


@pytest.mark.parametrize("cst", ["40", "41", "50"])
def test_accepts_non_taxed_icms_rule_without_nominal_rate(cst):
    payload = {
        "name": "PR exoneração integral diagnóstico",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": cst,
            "icms_tax_treatment_confirmed": False,
        },
    }

    result = ClientImportTaxRuleSchema().load(payload)

    assert result["configuration_json"]["icms_cst"] == cst
    assert "icms_rate" not in result["configuration_json"]


def test_rejects_nominal_rate_for_non_taxed_icms_rule():
    payload = {
        "name": "PR exoneração inválida",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": "40",
            "icms_rate": "19.5",
        },
    }

    with pytest.raises(ValidationError, match="não aceita alíquota nominal"):
        ClientImportTaxRuleSchema().load(payload)


@pytest.mark.parametrize(
    ("cst", "extra"),
    [
        (
            "10",
            {
                "icms_rate": "12",
                "icms_st_base_method": "4",
                "icms_st_mva_rate": "40",
                "icms_st_rate": "18",
            },
        ),
        ("20", {"icms_rate": "12", "icms_base_reduction_rate": "25"}),
        (
            "30",
            {
                "icms_st_base_method": "6",
                "icms_st_rate": "18",
            },
        ),
        (
            "60",
            {
                "icms_st_retained_base": "1000",
                "icms_st_retained_rate": "18",
                "icms_st_retained_value": "180",
            },
        ),
        (
            "70",
            {
                "icms_rate": "12",
                "icms_base_reduction_rate": "25",
                "icms_st_base_method": "4",
                "icms_st_mva_rate": "40",
                "icms_st_rate": "18",
            },
        ),
    ],
)
def test_accepts_additional_normal_regime_icms_csts(cst, extra):
    payload = {
        "name": f"Regra CST {cst}",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": cst,
            **extra,
        },
    }

    result = ClientImportTaxRuleSchema().load(payload)

    assert result["configuration_json"]["icms_cst"] == cst


def test_rejects_icms20_without_base_reduction():
    payload = {
        "name": "CST 20 incompleta",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": "20",
            "icms_rate": "12",
        },
    }

    with pytest.raises(ValidationError, match="exige icms_base_reduction_rate"):
        ClientImportTaxRuleSchema().load(payload)


def test_rejects_icms30_without_st_rate():
    payload = {
        "name": "CST 30 incompleta",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": "30",
            "icms_st_base_method": "6",
        },
    }

    with pytest.raises(ValidationError, match="icms_st_rate"):
        ClientImportTaxRuleSchema().load(payload)


def test_checkpoint_4a_defaults_new_operations_to_production():
    assert FetchDuimpSchema().load({})["provider_environment"] == "production"
    assert NfeWorkflowStateQuerySchema().load({})["environment"] == "production"
    draft = CreateNfeDraftFromDuimpSchema().load({})
    assert draft["environment"] == "production"
    assert draft["series"] == "1"
    assert draft["import_purpose"] is None


def test_checkpoint_4a_allows_client_first_process():
    process = CreateImportProcessSchema().load(
        {"importer_id": "11111111-1111-1111-1111-111111111111"}
    )
    assert process["reference_code"] is None
    assert process["duimp_number"] is None


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (FetchDuimpSchema(), {"provider_environment": "homologation"}),
        (CreateNfeDraftFromDuimpSchema(), {"environment": "homologation"}),
    ],
)
def test_checkpoint_4b_rejects_non_production_environments(schema, payload):
    with pytest.raises(ValidationError):
        schema.load(payload)


def test_tax_rule_rejects_reduction_and_deferment_together():
    payload = {
        "name": "Tratamento inválido",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": "51",
            "icms_base_reduction_rate": "100",
            "icms_deferment_rate": "100",
        },
    }
    with pytest.raises(ValidationError, match="não podem ser aplicados simultaneamente"):
        ClientImportTaxRuleSchema().load(payload)


def test_checkpoint_4b_keeps_legacy_workflow_environment_readable():
    result = NfeWorkflowStateQuerySchema().load({"environment": "homologation"})
    assert result["environment"] == "homologation"
