from decimal import Decimal, InvalidOperation

from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema
from marshmallow_sqlalchemy import SQLAlchemyAutoSchema, auto_field

from app.models.import_process import NfeNumberSequence

from ..models import (
    DuimpSnapshot,
    ExternalApiRequestLog,
    ExternalAuthType,
    ExternalConnectionStatus,
    ExternalProvider,
    ExternalProviderConnection,
    FiscalEnvironment,
    HttpMethod,
    ImportProcess,
    ImportProcessSource,
    ImportProcessStatus,
    ImportPurpose,
    NfeDraft,
    NfeDraftItem,
    NfeDraftStatus,
    NfeModel,
    NfeOperationType,
    NfePurpose,
    NfeXmlType,
    NfeXmlVersion,
)


class BaseAutoSchema(SQLAlchemyAutoSchema):
    class Meta:
        load_instance = False
        include_fk = True
        unknown = EXCLUDE


class ImportProcessSchema(BaseAutoSchema):
    class Meta(BaseAutoSchema.Meta):
        model = ImportProcess

    id = auto_field(dump_only=True)
    created_at = auto_field(dump_only=True)
    updated_at = auto_field(dump_only=True)


class ExternalProviderConnectionSchema(BaseAutoSchema):
    class Meta(BaseAutoSchema.Meta):
        model = ExternalProviderConnection

    id = auto_field(dump_only=True)
    created_at = auto_field(dump_only=True)
    updated_at = auto_field(dump_only=True)


class ExternalApiRequestLogSchema(BaseAutoSchema):
    class Meta(BaseAutoSchema.Meta):
        model = ExternalApiRequestLog

    id = auto_field(dump_only=True)
    started_at = auto_field(dump_only=True)
    finished_at = auto_field(dump_only=True)


class DuimpSnapshotSchema(BaseAutoSchema):
    class Meta(BaseAutoSchema.Meta):
        model = DuimpSnapshot

    id = auto_field(dump_only=True)
    fetched_at = auto_field(dump_only=True)
    created_at = auto_field(dump_only=True)


class NfeDraftSchema(BaseAutoSchema):
    class Meta(BaseAutoSchema.Meta):
        model = NfeDraft

    id = auto_field(dump_only=True)
    created_at = auto_field(dump_only=True)
    updated_at = auto_field(dump_only=True)


class NfeDraftItemSchema(BaseAutoSchema):
    class Meta(BaseAutoSchema.Meta):
        model = NfeDraftItem

    id = auto_field(dump_only=True)
    created_at = auto_field(dump_only=True)
    updated_at = auto_field(dump_only=True)


class NfeXmlVersionSchema(BaseAutoSchema):
    class Meta(BaseAutoSchema.Meta):
        model = NfeXmlVersion

    id = auto_field(dump_only=True)
    generated_at = auto_field(dump_only=True)


class ImportProcessListQuerySchema(Schema):
    status = fields.String(validate=validate.OneOf(ImportProcessStatus.values()), load_default=None)
    source = fields.String(validate=validate.OneOf(ImportProcessSource.values()), load_default=None)
    importer_id = fields.UUID(load_default=None)
    duimp_number = fields.String(load_default=None)
    q = fields.String(load_default=None)
    created_by_me = fields.Boolean(load_default=False)
    limit = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100))
    offset = fields.Integer(load_default=0, validate=validate.Range(min=0))


class NfeWorkflowStateQuerySchema(Schema):
    import_purpose = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(ImportPurpose.values()),
    )
    environment = fields.String(
        load_default=FiscalEnvironment.PRODUCTION.value,
        validate=validate.OneOf(FiscalEnvironment.values()),
    )
    series = fields.String(load_default="1", validate=validate.Length(min=1, max=10))


class CreateImportProcessSchema(Schema):
    importer_id = fields.UUID(required=True)
    reference_code = fields.String(load_default=None, allow_none=True, validate=validate.Length(min=1, max=80))
    duimp_number = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=50))
    duimp_version = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=20))
    source = fields.String(
        load_default=ImportProcessSource.MANUAL.value,
        validate=validate.OneOf(ImportProcessSource.values()),
    )


class UpdateImportProcessSchema(Schema):
    reference_code = fields.String(validate=validate.Length(min=1, max=80))
    duimp_number = fields.String(allow_none=True, validate=validate.Length(max=50))
    duimp_version = fields.String(allow_none=True, validate=validate.Length(max=20))
    status = fields.String(validate=validate.OneOf(ImportProcessStatus.values()))
    source = fields.String(validate=validate.OneOf(ImportProcessSource.values()))


class CreateProviderConnectionSchema(Schema):
    importer_id = fields.UUID(load_default=None, allow_none=True)
    provider = fields.String(required=True, validate=validate.OneOf(ExternalProvider.values()))
    environment = fields.String(
        required=True,
        validate=validate.OneOf(
            [FiscalEnvironment.PRODUCTION.value],
            error="Novas operações fiscais aceitam somente o ambiente production.",
        ),
    )
    auth_type = fields.String(required=True, validate=validate.OneOf(ExternalAuthType.values()))
    status = fields.String(
        load_default=ExternalConnectionStatus.ACTIVE.value,
        validate=validate.OneOf(ExternalConnectionStatus.values()),
    )
    config_json = fields.Dict(load_default=None, allow_none=True)
    credentials_ref = fields.String(load_default=None, allow_none=True)


class ProviderConnectionListQuerySchema(Schema):
    importer_id = fields.UUID(load_default=None)
    provider = fields.String(validate=validate.OneOf(ExternalProvider.values()), load_default=None)
    environment = fields.String(
        validate=validate.OneOf(FiscalEnvironment.values()),
        load_default=None,
    )
    status = fields.String(validate=validate.OneOf(ExternalConnectionStatus.values()), load_default=None)
    limit = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100))
    offset = fields.Integer(load_default=0, validate=validate.Range(min=0))


class CreateManualDuimpSnapshotSchema(Schema):
    duimp_number = fields.String(required=True)
    duimp_version = fields.String(load_default=None, allow_none=True)
    raw_payload = fields.Dict(required=True)
    normalized_payload = fields.Dict(load_default=None, allow_none=True)
    source_provider = fields.String(
        load_default=ExternalProvider.PORTAL_UNICO.value,
        validate=validate.OneOf(ExternalProvider.values()),
    )


class FetchDuimpSchema(Schema):
    provider_environment = fields.String(
        load_default=FiscalEnvironment.PRODUCTION.value,
        validate=validate.OneOf(
            [FiscalEnvironment.PRODUCTION.value],
            error="Novas operações fiscais aceitam somente o ambiente production.",
        ),
    )
    source_provider = fields.String(
        load_default=ExternalProvider.PORTAL_UNICO.value,
        validate=validate.OneOf([ExternalProvider.PORTAL_UNICO.value]),
    )
    duimp_payload = fields.Dict(load_default=None, allow_none=True)
    enrich_catalog = fields.Boolean(load_default=True)


class NfeDocumentOptionsSchema(Schema):
    operation_nature = fields.String(
        allow_none=True,
        validate=validate.Length(min=1, max=60),
    )
    presence_indicator = fields.String(
        allow_none=True,
        validate=validate.OneOf(["0", "1", "2", "3", "4", "5", "9"]),
    )
    intermediary_indicator = fields.String(
        allow_none=True,
        validate=validate.OneOf(["0", "1"]),
    )


class NfeItemDefaultsSchema(Schema):
    commercial_unit = fields.String(
        allow_none=True,
        validate=validate.Length(min=1, max=6),
    )
    taxable_unit = fields.String(
        allow_none=True,
        validate=validate.Length(min=1, max=6),
    )


class NfeTransportCarrierSchema(Schema):
    tax_id = fields.String(allow_none=True)
    name = fields.String(
        allow_none=True,
        validate=validate.Length(min=1, max=60),
    )
    state_registration = fields.String(allow_none=True)
    address = fields.String(allow_none=True)
    city_name = fields.String(allow_none=True)
    state = fields.String(
        allow_none=True,
        validate=validate.Length(equal=2),
    )


class NfeTransportVolumeSchema(Schema):
    quantity = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=1),
    )
    species = fields.String(allow_none=True)
    brand = fields.String(allow_none=True)
    numbering = fields.String(allow_none=True)
    net_weight = fields.Decimal(
        allow_none=True,
        as_string=True,
        validate=validate.Range(min=0),
    )
    gross_weight = fields.Decimal(
        allow_none=True,
        as_string=True,
        validate=validate.Range(min=0),
    )


class NfeTransportSchema(Schema):
    freight_mode = fields.String(
        validate=validate.OneOf(["0", "1", "2", "3", "4", "9"]),
    )
    carrier = fields.Nested(
        NfeTransportCarrierSchema,
        allow_none=True,
    )
    volume = fields.Nested(
        NfeTransportVolumeSchema,
        allow_none=True,
    )


class NfeDraftTransportUpdateSchema(NfeTransportSchema):
    carrier_id = fields.UUID(allow_none=True)

    @validates_schema
    def validate_carrier_source(self, data, **kwargs):
        if data.get("carrier_id") and data.get("carrier"):
            raise ValidationError(
                "Selecione uma transportadora cadastrada ou informe os dados manualmente.",
                field_name="carrier",
            )


class NfePaymentSchema(Schema):
    payment_indicator = fields.String(
        validate=validate.OneOf(["0", "1"]),
    )
    method = fields.String(validate=validate.Length(equal=2))
    description = fields.String(allow_none=True)
    value = fields.Decimal(allow_none=True, as_string=True)


class NfeAdditionalInfoSchema(Schema):
    automatic_summary = fields.Boolean()
    fiscal = fields.String(allow_none=True)
    complementary = fields.String(allow_none=True)
    legal_text = fields.String(allow_none=True)


class CreateNfeDraftFromDuimpSchema(Schema):
    environment = fields.String(
        load_default=FiscalEnvironment.PRODUCTION.value,
        validate=validate.OneOf(
            [FiscalEnvironment.PRODUCTION.value],
            error="Novas operações fiscais aceitam somente o ambiente production.",
        ),
    )
    series = fields.String(load_default="1", validate=validate.Length(min=1, max=10))
    number = fields.Integer(load_default=None, allow_none=True)
    import_purpose = fields.String(load_default=None, allow_none=True, validate=validate.OneOf(ImportPurpose.values()))
    source_provider = fields.String(
        load_default=ExternalProvider.PORTAL_UNICO.value,
        validate=validate.OneOf(ExternalProvider.values()),
    )
    provider_environment = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(
            [FiscalEnvironment.PRODUCTION.value],
            error="Novas operações fiscais aceitam somente o ambiente production.",
        ),
    )
    duimp_payload = fields.Dict(load_default=None, allow_none=True)
    duimp_snapshot_id = fields.UUID(load_default=None, allow_none=True)
    tax_configuration = fields.Dict(load_default=None, allow_none=True)
    tax_rule_id = fields.UUID(load_default=None, allow_none=True)
    additional_costs = fields.Dict(load_default=dict)
    foreign_supplier = fields.Dict(load_default=None, allow_none=True)
    duimp_overrides = fields.Dict(load_default=dict)
    document = fields.Nested(NfeDocumentOptionsSchema, load_default=dict)
    item_defaults = fields.Nested(NfeItemDefaultsSchema, load_default=dict)
    transport = fields.Nested(NfeTransportSchema, load_default=dict)
    payment = fields.Nested(NfePaymentSchema, load_default=dict)
    additional_info = fields.Nested(NfeAdditionalInfoSchema, load_default=dict)

    @validates_schema
    def validate_tax_source(self, data, **kwargs):
        if data.get("tax_configuration") is not None and data.get("tax_rule_id"):
            raise ValidationError(
                "Informe tax_configuration ou tax_rule_id, não ambos.",
                field_name="tax_rule_id",
            )


class CreateNfeDocumentPlanSchema(Schema):
    duimp_snapshot_id = fields.UUID(load_default=None, allow_none=True)
    additional_costs = fields.Dict(load_default=dict)

    @validates_schema
    def validate_additional_costs(self, data, **kwargs):
        allowed = {"afrmm", "siscomex_fee", "thc", "other"}
        costs = data.get("additional_costs") or {}
        unknown = sorted(set(costs) - allowed)
        if unknown:
            raise ValidationError(
                "Despesas não reconhecidas: " + ", ".join(unknown),
                field_name="additional_costs",
            )
        for name, value in costs.items():
            try:
                if value is not None and Decimal(str(value)) < 0:
                    raise ValidationError(
                        "Despesas compartilhadas não podem ser negativas.",
                        field_name=f"additional_costs.{name}",
                    )
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError(
                    "Informe um valor monetário válido.",
                    field_name=f"additional_costs.{name}",
                )


class GenerateNfeChildDraftsSchema(CreateNfeDraftFromDuimpSchema):
    import_purpose = fields.String(
        load_default=ImportPurpose.RESALE.value,
        validate=validate.OneOf(ImportPurpose.values()),
    )


class UpdateNfeDraftItemSchema(Schema):
    product_code = fields.String(validate=validate.Length(min=1, max=80))
    description = fields.String(validate=validate.Length(min=1, max=500))
    ncm = fields.String(validate=validate.Length(equal=8))
    cfop = fields.String(validate=validate.Length(equal=4))
    cest = fields.String(allow_none=True, validate=validate.Length(max=10))
    commercial_unit = fields.String(validate=validate.Length(min=1, max=20))
    commercial_quantity = fields.Decimal(as_string=True)
    commercial_unit_value = fields.Decimal(as_string=True)
    taxable_unit = fields.String(validate=validate.Length(min=1, max=20))
    taxable_quantity = fields.Decimal(as_string=True)
    taxable_unit_value = fields.Decimal(as_string=True)
    product_value = fields.Decimal(as_string=True)
    freight_value = fields.Decimal(as_string=True)
    insurance_value = fields.Decimal(as_string=True)
    discount_value = fields.Decimal(as_string=True)
    other_value = fields.Decimal(as_string=True)
    import_payload = fields.Dict(allow_none=True)


class NfeDraftAdditionalCostsSchema(Schema):
    afrmm = fields.Decimal(as_string=True, validate=validate.Range(min=0))
    siscomex_fee = fields.Decimal(as_string=True, validate=validate.Range(min=0))
    thc = fields.Decimal(as_string=True, validate=validate.Range(min=0))
    other = fields.Decimal(as_string=True, validate=validate.Range(min=0))


class NfeDraftIcmsAdjustmentSchema(Schema):
    cst = fields.String(
        required=True,
        validate=validate.OneOf(["00", "40", "41", "50", "51", "90"]),
    )
    base = fields.Decimal(required=True, as_string=True, validate=validate.Range(min=0))
    rate = fields.Decimal(allow_none=True, as_string=True, validate=validate.Range(min=0, max=100))
    reduction_rate = fields.Decimal(allow_none=True, as_string=True, validate=validate.Range(min=0, max=100))
    deferment_rate = fields.Decimal(allow_none=True, as_string=True, validate=validate.Range(min=0, max=100))


class NfeDraftTaxAdjustmentSchema(Schema):
    source = fields.String(
        load_default="manual_adjustment",
        validate=validate.OneOf(["manual_adjustment", "tax_rule"]),
    )
    reason = fields.String(
        required=True,
        validate=validate.Length(min=10, max=500),
    )
    cfop = fields.String(validate=validate.Regexp(r"^3\d{3}$"))
    icms = fields.Nested(NfeDraftIcmsAdjustmentSchema)

    @validates_schema
    def validate_adjustment(self, data, **kwargs):
        if data.get("source") == "manual_adjustment" and not data.get("icms"):
            raise ValidationError(
                "Informe os dados do ICMS para o ajuste manual.",
                field_name="icms",
            )


class DeleteNfeDraftSchema(Schema):
    reason = fields.String(
        required=True,
        validate=validate.Length(min=10, max=500),
    )


class NfeIssuerUpdateSchema(Schema):
    state_registration = fields.String(
        allow_none=True,
        validate=validate.Length(min=2, max=20),
    )


class NfeForeignSupplierAddressUpdateSchema(Schema):
    street = fields.String(allow_none=True, validate=validate.Length(max=60))
    number = fields.String(allow_none=True, validate=validate.Length(max=60))
    complement = fields.String(allow_none=True, validate=validate.Length(max=60))
    district = fields.String(allow_none=True, validate=validate.Length(max=60))
    city_name = fields.String(allow_none=True, validate=validate.Length(max=60))


class NfeForeignSupplierUpdateSchema(Schema):
    legal_name = fields.String(
        allow_none=True,
        validate=validate.Length(min=1, max=60),
    )
    foreign_id = fields.String(allow_none=True, validate=validate.Length(max=20))
    country_code = fields.String(
        allow_none=True,
        validate=validate.Length(min=1, max=4),
    )
    country_name = fields.String(
        allow_none=True,
        validate=validate.Length(min=1, max=60),
    )
    country_iso_alpha_2 = fields.String(
        allow_none=True,
        validate=validate.Length(equal=2),
    )
    address = fields.Nested(
        NfeForeignSupplierAddressUpdateSchema,
        allow_none=True,
    )


class UpdateNfeDraftSchema(Schema):
    document = fields.Nested(NfeDocumentOptionsSchema)
    issuer = fields.Nested(NfeIssuerUpdateSchema)
    foreign_supplier = fields.Nested(NfeForeignSupplierUpdateSchema)
    item_defaults = fields.Nested(NfeItemDefaultsSchema)
    transport = fields.Nested(NfeDraftTransportUpdateSchema)
    payment = fields.Nested(NfePaymentSchema)
    additional_info = fields.Nested(NfeAdditionalInfoSchema)
    additional_costs = fields.Nested(NfeDraftAdditionalCostsSchema)

    @validates_schema
    def validate_has_changes(self, data, **kwargs):
        if not data:
            raise ValidationError("Informe ao menos uma seção para atualizar.")


class GenerateXmlSchema(Schema):
    xml_type = fields.String(
        load_default=NfeXmlType.UNSIGNED.value,
        validate=validate.OneOf([NfeXmlType.UNSIGNED.value]),
    )


class MetadataSchema(Schema):
    import_process_statuses = fields.List(fields.String(), dump_default=ImportProcessStatus.values())
    import_process_sources = fields.List(fields.String(), dump_default=ImportProcessSource.values())
    external_providers = fields.List(fields.String(), dump_default=ExternalProvider.values())
    fiscal_environments = fields.List(fields.String(), dump_default=FiscalEnvironment.values())
    external_auth_types = fields.List(fields.String(), dump_default=ExternalAuthType.values())
    external_connection_statuses = fields.List(fields.String(), dump_default=ExternalConnectionStatus.values())
    http_methods = fields.List(fields.String(), dump_default=HttpMethod.values())
    nfe_models = fields.List(fields.String(), dump_default=NfeModel.values())
    nfe_purposes = fields.List(fields.String(), dump_default=NfePurpose.values())
    nfe_operation_types = fields.List(fields.String(), dump_default=NfeOperationType.values())
    nfe_draft_statuses = fields.List(fields.String(), dump_default=NfeDraftStatus.values())
    nfe_xml_types = fields.List(fields.String(), dump_default=NfeXmlType.values())
    import_purposes = fields.List(fields.String(), dump_default=ImportPurpose.values())

class NfeNumberSequenceSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = NfeNumberSequence
        load_instance = False
        include_fk = True

    id = fields.UUID(dump_only=True)
    organization_id = fields.UUID(dump_only=True)
    client_id = fields.UUID(required=True)

    environment = fields.Function(
        lambda obj: getattr(obj.environment, "value", obj.environment),
        dump_only=True,
    )

    model = fields.String(
        load_default="55",
        validate=validate.OneOf(["55"]),
    )

    series = fields.String(required=True)
    current_number = fields.Integer(load_default=0)
    initial_number = fields.Integer(load_default=1)
    max_number = fields.Integer(load_default=999999999)

    status = fields.Function(
        lambda obj: getattr(obj.status, "value", obj.status),
        dump_only=True,
    )

    last_reserved_number = fields.Integer(dump_only=True)
    last_reserved_at = fields.DateTime(dump_only=True)

    created_by_user_id = fields.UUID(dump_only=True)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)


class UpsertNfeNumberSequenceSchema(Schema):
    environment = fields.String(
        required=True,
        validate=validate.OneOf(
            ["production"],
            error="A sequência fiscal aceita somente o ambiente production.",
        ),
    )

    model = fields.String(
        load_default="55",
        validate=validate.OneOf(["55"]),
    )

    series = fields.String(required=True)
    current_number = fields.Integer(
        load_default=None,
        allow_none=True,
        validate=validate.Range(min=0, max=999999999),
    )
    initial_number = fields.Integer(
        load_default=1,
        validate=validate.Range(min=1, max=999999999),
    )
    max_number = fields.Integer(
        load_default=999999999,
        validate=validate.Range(min=1, max=999999999),
    )

    status = fields.String(
        load_default="active",
        validate=validate.OneOf(["active", "inactive"]),
    )
