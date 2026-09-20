from decimal import Decimal, InvalidOperation

from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema

from ..models import FiscalEnvironment, ImportPurpose
from ..nfe_reference import NFE_TRANSPORT_MODE_CODES
from .import_process import (
    NfeAdditionalInfoSchema,
    NfeDocumentOptionsSchema,
    NfeItemDefaultsSchema,
    NfePaymentSchema,
    NfeTransportSchema,
)


class ClientImportTaxRuleSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    id = fields.UUID(dump_only=True)
    organization_id = fields.UUID(dump_only=True)
    client_id = fields.UUID(dump_only=True)
    name = fields.String(required=True, validate=validate.Length(min=1, max=120))
    issuer_state = fields.String(
        required=True,
        validate=validate.Regexp(r"^[A-Z]{2}$"),
    )
    import_purpose = fields.String(
        required=True,
        validate=validate.OneOf(ImportPurpose.values()),
    )
    import_modality = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(["direct", "on_behalf", "by_order"]),
    )
    tax_regime = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(["1", "2", "3"]),
    )
    ncm_pattern = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.Regexp(r"^[0-9]{2,8}$"),
    )
    ncm_scope_type = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(["all", "prefix", "exact"]),
    )
    ncm_patterns = fields.List(
        fields.String(validate=validate.Regexp(r"^[0-9]{2,8}$")),
        load_default=None,
        allow_none=True,
    )
    priority = fields.Integer(
        load_default=0,
        validate=validate.Range(min=0, max=1000000),
    )
    revision = fields.Integer(dump_only=True)
    configuration_json = fields.Dict(required=True)
    additional_cost_defaults = fields.Dict(load_default=None, allow_none=True)
    transport_defaults = fields.Dict(load_default=None, allow_none=True)
    payment_defaults = fields.Dict(load_default=None, allow_none=True)
    active = fields.Boolean(load_default=True)
    effective_from = fields.Date(load_default=None, allow_none=True)
    effective_until = fields.Date(load_default=None, allow_none=True)
    created_by_user_id = fields.UUID(dump_only=True)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

    @validates_schema
    def validate_rule(self, data, **kwargs):
        self._normalize_ncm_scope(data)
        start = data.get("effective_from")
        end = data.get("effective_until")
        if start and end and end < start:
            raise ValidationError(
                "effective_until não pode ser anterior a effective_from.",
                field_name="effective_until",
            )

        if data.get("transport_defaults") is not None:
            NfeTransportSchema().load(data["transport_defaults"])
        if data.get("payment_defaults") is not None:
            NfePaymentSchema().load(data["payment_defaults"])

        configuration = data.get("configuration_json")
        if configuration is None:
            return
        nested_defaults = (
            (
                "document_defaults",
                NfeDocumentOptionsSchema(),
            ),
            ("item_defaults", NfeItemDefaultsSchema()),
            ("additional_info_defaults", NfeAdditionalInfoSchema()),
        )
        for field_name, schema in nested_defaults:
            if configuration.get(field_name) is not None:
                try:
                    schema.load(configuration[field_name])
                except ValidationError as exc:
                    raise ValidationError(
                        {field_name: exc.messages},
                        field_name="configuration_json",
                    ) from exc
        cfop = str(configuration.get("cfop") or "")
        if not cfop.isdigit() or len(cfop) != 4:
            raise ValidationError(
                "configuration_json.cfop deve conter 4 dígitos.",
                field_name="configuration_json",
            )
        origin = str(configuration.get("icms_origin") or "")
        if origin not in set("012345678"):
            raise ValidationError(
                "configuration_json.icms_origin deve ser um dígito entre 0 e 8.",
                field_name="configuration_json",
            )
        for tax_field in ("ipi_cst", "ipi_zero_rate_cst", "pis_cst", "cofins_cst"):
            tax_code = configuration.get(tax_field)
            if tax_code is not None and (
                not str(tax_code).isdigit() or len(str(tax_code)) != 2
            ):
                raise ValidationError(
                    f"configuration_json.{tax_field} deve conter 2 dígitos.",
                    field_name="configuration_json",
                )

        def decimal_rate(field_name):
            raw_value = configuration.get(field_name)
            if raw_value in (None, ""):
                return Decimal("0")
            try:
                value = Decimal(str(raw_value))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError(
                    f"configuration_json.{field_name} deve ser numérico.",
                    field_name="configuration_json",
                )
            if not Decimal("0") <= value <= Decimal("100"):
                raise ValidationError(
                    f"configuration_json.{field_name} deve estar entre 0 e 100.",
                    field_name="configuration_json",
                )
            return value

        base_reduction_rate = decimal_rate("icms_base_reduction_rate")
        deferment_rate = decimal_rate("icms_deferment_rate")
        raw_st_mva_rate = configuration.get("icms_st_mva_rate")
        if raw_st_mva_rate in (None, ""):
            st_mva_rate = Decimal("0")
        else:
            try:
                st_mva_rate = Decimal(str(raw_st_mva_rate))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError(
                    "configuration_json.icms_st_mva_rate deve ser numérico.",
                    field_name="configuration_json",
                )
        decimal_rate("icms_st_base_reduction_rate")
        if base_reduction_rate > 0 and deferment_rate > 0:
            raise ValidationError(
                "Redução de base e diferimento de ICMS não podem ser aplicados simultaneamente.",
                field_name="configuration_json",
            )

        cst = str(configuration.get("icms_cst") or "90").zfill(2)
        supported_csts = {
            "00",
            "10",
            "20",
            "30",
            "40",
            "41",
            "50",
            "51",
            "60",
            "70",
            "90",
        }
        if cst not in supported_csts:
            raise ValidationError(
                "configuration_json.icms_cst deve ser 00, 10, 20, 30, 40, 41, "
                "50, 51, 60, 70 ou 90.",
                field_name="configuration_json",
            )
        raw_rate = configuration.get("icms_rate")
        raw_confirmed = configuration.get("icms_tax_treatment_confirmed")
        if raw_confirmed not in (None, True, False):
            raise ValidationError(
                "configuration_json.icms_tax_treatment_confirmed deve ser "
                "booleano.",
                field_name="configuration_json",
            )
        if cst in {"40", "41", "50", "30", "60"}:
            if raw_rate not in (None, ""):
                raise ValidationError(
                    f"ICMS CST {cst} não aceita alíquota nominal.",
                    field_name="configuration_json",
                )
            if cst in {"40", "41", "50"}:
                return

        if cst in {"20", "70"} and base_reduction_rate <= 0:
            raise ValidationError(
                f"ICMS CST {cst} exige icms_base_reduction_rate maior que zero.",
                field_name="configuration_json",
            )

        if cst in {"10", "30", "70"}:
            st_base_method = str(
                configuration.get("icms_st_base_method") or "6"
            )
            if st_base_method not in {"4", "6"}:
                raise ValidationError(
                    "configuration_json.icms_st_base_method deve ser 4 (MVA) "
                    "ou 6 (valor da operação).",
                    field_name="configuration_json",
                )
            if (
                st_base_method == "4"
                and configuration.get("icms_st_mva_rate") in (None, "")
            ):
                raise ValidationError(
                    "ICMS ST com modalidade 4 exige icms_st_mva_rate.",
                    field_name="configuration_json",
                )
            if not Decimal("0") <= st_mva_rate <= Decimal("999.9999"):
                raise ValidationError(
                    "configuration_json.icms_st_mva_rate deve estar entre 0 e "
                    "999.9999.",
                    field_name="configuration_json",
                )
            raw_st_rate = configuration.get("icms_st_rate")
            try:
                st_rate = Decimal(str(raw_st_rate))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError(
                    "configuration_json.icms_st_rate deve ser numérico.",
                    field_name="configuration_json",
                )
            if not Decimal("0") < st_rate < Decimal("100"):
                raise ValidationError(
                    "configuration_json.icms_st_rate deve ser maior que 0 e "
                    "menor que 100.",
                    field_name="configuration_json",
                )

        if cst == "60":
            retained_fields = (
                "icms_st_retained_base",
                "icms_st_retained_rate",
                "icms_st_retained_value",
            )
            if any(configuration.get(field) in (None, "") for field in retained_fields):
                raise ValidationError(
                    "ICMS CST 60 exige base, alíquota e valor do ICMS ST retido "
                    "anteriormente.",
                    field_name="configuration_json",
                )
            for field in retained_fields:
                try:
                    retained_value = Decimal(str(configuration[field]))
                except (InvalidOperation, TypeError, ValueError):
                    raise ValidationError(
                        f"configuration_json.{field} deve ser numérico.",
                        field_name="configuration_json",
                    )
                if retained_value < 0:
                    raise ValidationError(
                        f"configuration_json.{field} não pode ser negativo.",
                        field_name="configuration_json",
                    )
            if Decimal(str(configuration["icms_st_retained_rate"])) > Decimal(
                "100"
            ):
                raise ValidationError(
                    "configuration_json.icms_st_retained_rate deve ser menor "
                    "ou igual a 100.",
                    field_name="configuration_json",
                )
            return

        if raw_rate in (None, "") and cst == "51":
            if (
                deferment_rate != Decimal("100")
                and base_reduction_rate != Decimal("100")
            ):
                raise ValidationError(
                    "ICMS CST 51 sem alíquota nominal somente é aceito para "
                    "XML diagnóstico com diferimento ou redução de base de 100%.",
                    field_name="configuration_json",
                )
            return
        if raw_rate in (None, "") and cst == "30":
            return
        try:
            rate = Decimal(str(raw_rate))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError(
                "configuration_json.icms_rate deve ser numérico.",
                field_name="configuration_json",
            )
        if not Decimal("0") < rate < Decimal("100"):
            raise ValidationError(
                "configuration_json.icms_rate deve ser maior que 0 e menor que 100.",
                field_name="configuration_json",
            )

    @staticmethod
    def _normalize_ncm_scope(data):
        scope_fields = {"ncm_scope_type", "ncm_patterns", "ncm_pattern"}
        if not scope_fields.intersection(data):
            return

        legacy_pattern = data.get("ncm_pattern")
        patterns = data.get("ncm_patterns")
        if patterns is None:
            patterns = [legacy_pattern] if legacy_pattern else []
        patterns = sorted({str(pattern) for pattern in patterns})

        scope_type = data.get("ncm_scope_type")
        if scope_type is None:
            if not patterns:
                scope_type = "all"
            elif all(len(pattern) == 8 for pattern in patterns):
                scope_type = "exact"
            else:
                scope_type = "prefix"

        if scope_type == "all":
            if patterns:
                raise ValidationError(
                    "Regras para todos os NCMs não aceitam ncm_patterns.",
                    field_name="ncm_patterns",
                )
        elif not patterns:
            raise ValidationError(
                "Informe ao menos um NCM para o escopo selecionado.",
                field_name="ncm_patterns",
            )
        elif scope_type == "exact" and any(
            len(pattern) != 8 for pattern in patterns
        ):
            raise ValidationError(
                "O escopo exact aceita somente NCMs completos com 8 dígitos.",
                field_name="ncm_patterns",
            )
        elif scope_type == "prefix" and any(
            not 2 <= len(pattern) <= 7 for pattern in patterns
        ):
            raise ValidationError(
                "O escopo prefix aceita prefixos de NCM com 2 a 7 dígitos.",
                field_name="ncm_patterns",
            )

        if legacy_pattern and patterns != [legacy_pattern]:
            raise ValidationError(
                "ncm_pattern deve representar o mesmo escopo de ncm_patterns.",
                field_name="ncm_pattern",
            )

        data["ncm_scope_type"] = scope_type
        data["ncm_patterns"] = patterns
        data["ncm_pattern"] = patterns[0] if len(patterns) == 1 else None


class UpdateClientImportTaxRuleSchema(ClientImportTaxRuleSchema):
    name = fields.String(validate=validate.Length(min=1, max=120))
    issuer_state = fields.String(validate=validate.Regexp(r"^[A-Z]{2}$"))
    import_purpose = fields.String(validate=validate.OneOf(ImportPurpose.values()))
    configuration_json = fields.Dict()


class SimulateClientImportTaxRuleSchema(Schema):
    issuer_state = fields.String(
        required=True,
        validate=validate.Regexp(r"^[A-Z]{2}$"),
    )
    tax_regime = fields.String(
        required=True,
        validate=validate.OneOf(["1", "2", "3"]),
    )
    import_purpose = fields.String(
        required=True,
        validate=validate.OneOf(ImportPurpose.values()),
    )
    import_modality = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(["direct", "on_behalf", "by_order"]),
    )
    ncm = fields.String(
        required=True,
        validate=validate.Regexp(r"^[0-9]{8}$"),
    )
    reference_date = fields.Date(load_default=None, allow_none=True)
    tax_rule_id = fields.UUID(load_default=None, allow_none=True)


class NfeContextQuerySchema(Schema):
    duimp_snapshot_id = fields.UUID(load_default=None, allow_none=True)
    import_purpose = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(ImportPurpose.values()),
    )
    provider_environment = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(
            [FiscalEnvironment.PRODUCTION.value],
            error="Novas operações fiscais aceitam somente o ambiente production.",
        ),
    )
    refresh_external = fields.Boolean(load_default=False)


class ResolveNfeContextSchema(NfeContextQuerySchema):
    refresh_external = fields.Boolean(load_default=True)
    overrides = fields.Dict(load_default=dict)

    @validates_schema
    def validate_transport_mode_code(self, data, **kwargs):
        overrides = data.get("overrides") or {}
        value = overrides.get("transport_mode_code")
        if value in (None, ""):
            return
        if str(value).strip() not in NFE_TRANSPORT_MODE_CODES:
            raise ValidationError(
                {
                    "transport_mode_code": [
                        "Selecione uma via de transporte válida (códigos 1 a 13)."
                    ]
                },
                field_name="overrides",
            )


class NfeItemClassificationQuerySchema(Schema):
    duimp_snapshot_id = fields.UUID(load_default=None, allow_none=True)


class NfeItemClassificationInputSchema(Schema):
    duimp_item_number = fields.String(
        required=True,
        validate=validate.Length(min=1, max=30),
    )
    import_purpose = fields.String(
        required=True,
        validate=validate.OneOf(ImportPurpose.values()),
    )
    tax_rule_id = fields.UUID(load_default=None, allow_none=True)


class BulkNfeItemClassificationSchema(Schema):
    duimp_snapshot_id = fields.UUID(load_default=None, allow_none=True)
    items = fields.List(
        fields.Nested(NfeItemClassificationInputSchema),
        required=True,
        validate=validate.Length(min=1),
    )

    @validates_schema
    def validate_unique_items(self, data, **kwargs):
        numbers = [
            item["duimp_item_number"]
            for item in data.get("items") or []
        ]
        if len(numbers) != len(set(numbers)):
            raise ValidationError(
                "Cada item da DUIMP deve aparecer apenas uma vez.",
                field_name="items",
            )
