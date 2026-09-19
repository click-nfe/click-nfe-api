from marshmallow import ValidationError
import pytest

from app.models import ClientImportTaxRule, NfeItemClassification
from app.schemas.nfe_automation import ClientImportTaxRuleSchema
from app.services.import_process import ImportNfeService


def tax_rule(**overrides):
    values = {
        "name": "Regra padrão",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "import_modality": "direct",
        "tax_regime": "3",
        "ncm_scope_type": "all",
        "ncm_patterns": [],
        "ncm_pattern": None,
        "priority": 100,
        "active": True,
        "revision": 1,
    }
    values.update(overrides)
    return ClientImportTaxRule(**values)


def test_ncm_specificity_precedes_numeric_priority():
    default_rule = tax_rule(priority=1000)
    prefix_rule = tax_rule(
        ncm_scope_type="prefix",
        ncm_patterns=["84"],
        ncm_pattern="84",
        priority=100,
    )
    exact_rule = tax_rule(
        ncm_scope_type="exact",
        ncm_patterns=["84212300"],
        ncm_pattern="84212300",
        priority=1,
    )

    ncm = "84212300"
    assert ImportNfeService._tax_rule_score(exact_rule, ncm) > (
        ImportNfeService._tax_rule_score(prefix_rule, ncm)
    )
    assert ImportNfeService._tax_rule_score(prefix_rule, ncm) > (
        ImportNfeService._tax_rule_score(default_rule, ncm)
    )


def test_equal_specific_scope_is_ambiguous_but_fallback_is_not():
    first = tax_rule(
        ncm_scope_type="exact",
        ncm_patterns=["84212300", "84713012"],
        ncm_pattern=None,
    )
    same_ncm = tax_rule(
        ncm_scope_type="exact",
        ncm_patterns=["84212300"],
        ncm_pattern="84212300",
    )
    fallback = tax_rule()

    assert ImportNfeService._tax_rules_are_ambiguous(first, same_ncm) is True
    assert ImportNfeService._tax_rules_are_ambiguous(first, fallback) is False


def test_schema_normalizes_legacy_and_multiple_exact_ncms():
    schema = ClientImportTaxRuleSchema()
    base = {
        "name": "Regra",
        "issuer_state": "PR",
        "import_purpose": "resale",
        "configuration_json": {
            "cfop": "3102",
            "icms_origin": "1",
            "icms_cst": "90",
            "icms_rate": "12",
        },
    }

    legacy = schema.load({**base, "ncm_pattern": "84"})
    assert legacy["ncm_scope_type"] == "prefix"
    assert legacy["ncm_patterns"] == ["84"]

    exact = schema.load(
        {
            **base,
            "ncm_scope_type": "exact",
            "ncm_patterns": ["84713012", "84212300", "84212300"],
        }
    )
    assert exact["ncm_patterns"] == ["84212300", "84713012"]
    assert exact["ncm_pattern"] is None


def test_schema_rejects_exact_scope_with_prefix():
    with pytest.raises(ValidationError):
        ClientImportTaxRuleSchema().load(
            {
                "name": "Regra inválida",
                "issuer_state": "PR",
                "import_purpose": "resale",
                "ncm_scope_type": "exact",
                "ncm_patterns": ["84"],
                "configuration_json": {
                    "cfop": "3102",
                    "icms_origin": "1",
                    "icms_cst": "90",
                    "icms_rate": "12",
                },
            }
        )


def test_classification_uses_immutable_rule_snapshot():
    live_rule = tax_rule(
        configuration_json={"cfop": "3102", "icms_rate": "18"}
    )
    classification = NfeItemClassification(
        tax_rule_snapshot={
            "revision": 1,
            "configuration_json": {"cfop": "3102", "icms_rate": "12"},
        }
    )
    classification.tax_rule = live_rule

    configuration = ImportNfeService._classification_tax_configuration(
        classification,
        {},
    )

    assert configuration["icms_rate"] == "12"
