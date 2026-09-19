from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.utils import uuid_pk
from ..extensions import Base


class EnumMixin(str, Enum):
    """Base para enums persistidos como string no banco."""

    @classmethod
    def values(cls) -> list[str]:
        return [item.value for item in cls]


class ImportProcessStatus(EnumMixin):
    CREATED = "created"
    DUIMP_FETCHING = "duimp_fetching"
    DUIMP_FETCHED = "duimp_fetched"
    DUIMP_FETCH_FAILED = "duimp_fetch_failed"
    DUIMP_NORMALIZED = "duimp_normalized"
    FISCAL_DRAFT_CREATED = "fiscal_draft_created"
    DRAFT_VALIDATION_FAILED = "draft_validation_failed"
    DRAFT_READY = "draft_ready"
    XML_GENERATED = "xml_generated"
    XML_VALIDATION_FAILED = "xml_validation_failed"
    XML_VALIDATED = "xml_validated"
    XML_SIGNED = "xml_signed"
    TRANSMISSION_PENDING = "transmission_pending"
    TRANSMITTED = "transmitted"
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ImportProcessSource(EnumMixin):
    MANUAL = "manual"
    PORTAL_UNICO = "portal_unico"
    GETT_IMPORT = "gett_import"
    API = "api"


class ExternalProvider(EnumMixin):
    PORTAL_UNICO = "portal_unico"
    SERPRO = "serpro"
    GETT = "gett"
    SEFAZ = "sefaz"
    PRIVATE_FISCAL_API = "private_fiscal_api"


class FiscalEnvironment(EnumMixin):
    HOMOLOGATION = "homologation"
    PRODUCTION = "production"


class ExternalAuthType(EnumMixin):
    CERTIFICATE = "certificate"
    OAUTH = "oauth"
    API_KEY = "api_key"
    CERTIFICATE_PLUS_TOKEN = "certificate_plus_token"


class ExternalConnectionStatus(EnumMixin):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"
    ERROR = "error"


class HttpMethod(EnumMixin):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class NfeModel(EnumMixin):
    NFE = "55"


class NfePurpose(EnumMixin):
    NORMAL = "normal"
    COMPLEMENTARY = "complementary"
    ADJUSTMENT = "adjustment"
    RETURN = "return"


class NfeOperationType(EnumMixin):
    ENTRY = "entry"
    EXIT = "exit"


class NfeDraftStatus(EnumMixin):
    DRAFT = "draft"
    VALIDATION_FAILED = "validation_failed"
    READY_FOR_XML = "ready_for_xml"
    XML_GENERATED = "xml_generated"
    SIGNED = "signed"
    TRANSMITTED = "transmitted"
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class NfeXmlType(EnumMixin):
    UNSIGNED = "unsigned"
    SIGNED = "signed"
    AUTHORIZED = "authorized"
    CANCELLED_EVENT = "cancelled_event"
    CORRECTION_LETTER_EVENT = "correction_letter_event"


class ImportPurpose(EnumMixin):
    RESALE = "resale"
    INDUSTRIALIZATION = "industrialization"
    FIXED_ASSET = "fixed_asset"
    USE_CONSUMPTION = "use_consumption"


def enum_column(
    enum_cls,
    *,
    name: str | None = None,
    nullable: bool = False,
    default=None,
    length: int = 50,
    index: bool = False,
):
    """Cria Enum como VARCHAR + CHECK constraint, amigável para Alembic/Postgres."""
    return Column(
        SAEnum(
            enum_cls,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            length=length,
            name=name or enum_cls.__name__.lower(),
            validate_strings=True,
        ),
        nullable=nullable,
        default=default,
        index=index,
    )


class ImportProcess(Base):
    __tablename__ = "import_processes"

    id = uuid_pk()

    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    importer_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True)

    reference_code = Column(String(80), nullable=False, index=True)
    duimp_number = Column(String(50), nullable=True, index=True)
    duimp_version = Column(String(20), nullable=True)

    status = enum_column(
        ImportProcessStatus,
        name="import_process_status",
        default=ImportProcessStatus.CREATED.value,
    )
    source = enum_column(
        ImportProcessSource,
        name="import_process_source",
        default=ImportProcessSource.MANUAL.value,
    )

    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    importer = relationship("Client", foreign_keys=[importer_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    snapshots = relationship("DuimpSnapshot", back_populates="import_process", lazy=True, cascade="all, delete-orphan")
    nfe_drafts = relationship("NfeDraft", back_populates="import_process", lazy=True, cascade="all, delete-orphan")
    document_plans = relationship(
        "NfeDocumentPlan",
        back_populates="import_process",
        lazy=True,
        cascade="all, delete-orphan",
    )
    api_logs = relationship("ExternalApiRequestLog", back_populates="import_process", lazy=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "reference_code", name="uq_import_process_org_reference"),
    )


class ExternalProviderConnection(Base):
    __tablename__ = "external_provider_connections"

    id = uuid_pk()

    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    importer_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True, index=True)

    provider = enum_column(ExternalProvider, name="external_provider")
    environment = enum_column(FiscalEnvironment, name="fiscal_environment")
    auth_type = enum_column(ExternalAuthType, name="external_auth_type")
    status = enum_column(
        ExternalConnectionStatus,
        name="external_connection_status",
        default=ExternalConnectionStatus.ACTIVE.value,
    )

    config_json = Column(JSON, nullable=True)
    credentials_ref = Column(String(255), nullable=True)

    last_healthcheck_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    importer = relationship("Client", foreign_keys=[importer_id])

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "importer_id",
            "provider",
            "environment",
            name="uq_external_provider_connection_context",
        ),
    )


class ExternalApiRequestLog(Base):
    __tablename__ = "external_api_request_logs"

    id = uuid_pk()

    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    import_process_id = Column(UUID(as_uuid=True), ForeignKey("import_processes.id"), nullable=True, index=True)

    provider = enum_column(ExternalProvider, name="external_api_log_provider")
    endpoint_name = Column(String(100), nullable=False)
    method = enum_column(HttpMethod, name="external_api_http_method", length=10)

    request_payload = Column(JSON, nullable=True)
    response_payload = Column(JSON, nullable=True)

    status_code = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=False, default=False)

    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)

    correlation_id = Column(String(100), nullable=True)
    external_protocol = Column(String(100), nullable=True)

    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    organization = relationship("Organization")
    import_process = relationship("ImportProcess", back_populates="api_logs")


class DuimpSnapshot(Base):
    __tablename__ = "duimp_snapshots"

    id = uuid_pk()

    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    import_process_id = Column(UUID(as_uuid=True), ForeignKey("import_processes.id"), nullable=False, index=True)

    duimp_number = Column(String(50), nullable=False, index=True)
    duimp_version = Column(String(20), nullable=True)

    raw_payload = Column(JSON, nullable=False)
    normalized_payload = Column(JSON, nullable=True)

    source_provider = enum_column(ExternalProvider, name="duimp_snapshot_source_provider")
    fetched_at = Column(DateTime, nullable=False)

    checksum = Column(String(128), nullable=True)

    created_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    import_process = relationship("ImportProcess", back_populates="snapshots")


class NfeItemClassification(Base):
    __tablename__ = "nfe_item_classifications"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    import_process_id = Column(
        UUID(as_uuid=True),
        ForeignKey("import_processes.id"),
        nullable=False,
        index=True,
    )
    duimp_snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("duimp_snapshots.id"),
        nullable=False,
        index=True,
    )
    duimp_item_number = Column(String(30), nullable=False)
    import_purpose = Column(String(30), nullable=False, index=True)
    tax_rule_id = Column(
        UUID(as_uuid=True),
        ForeignKey("client_import_tax_rules.id"),
        nullable=True,
        index=True,
    )
    tax_rule_snapshot = Column(JSON, nullable=True)
    cfop = Column(String(4), nullable=True)
    source = Column(String(20), nullable=False, default="manual")
    classified_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    import_process = relationship("ImportProcess")
    duimp_snapshot = relationship("DuimpSnapshot")
    tax_rule = relationship("ClientImportTaxRule")
    classified_by = relationship("User", foreign_keys=[classified_by_user_id])

    __table_args__ = (
        UniqueConstraint(
            "duimp_snapshot_id",
            "duimp_item_number",
            name="uq_nfe_item_classification_snapshot_item",
        ),
    )


class NfeDocumentPlan(Base):
    """Master gerencial que planeja as NF-e filhas de um snapshot."""

    __tablename__ = "nfe_document_plans"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    import_process_id = Column(
        UUID(as_uuid=True),
        ForeignKey("import_processes.id"),
        nullable=False,
        index=True,
    )
    duimp_snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("duimp_snapshots.id"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="planned", index=True)
    allocation_basis = Column(
        String(40),
        nullable=False,
        default="customs_value",
    )
    shared_costs = Column(JSON, nullable=False)
    totals = Column(JSON, nullable=False)
    reconciliation = Column(JSON, nullable=False)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    import_process = relationship("ImportProcess", back_populates="document_plans")
    duimp_snapshot = relationship("DuimpSnapshot")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    documents = relationship(
        "NfePlannedDocument",
        back_populates="document_plan",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="NfePlannedDocument.ordinal",
    )

    __table_args__ = (
        UniqueConstraint(
            "duimp_snapshot_id",
            "version_number",
            name="uq_nfe_document_plan_snapshot_version",
        ),
    )


class NfePlannedDocument(Base):
    """NF-e filha planejada; seus artefatos fiscais vivem em rascunhos próprios."""

    __tablename__ = "nfe_planned_documents"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    document_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_document_plans.id"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    exporter_key = Column(String(255), nullable=False)
    exporter_code = Column(String(100), nullable=True, index=True)
    foreign_supplier = Column(JSON, nullable=True)
    operation_nature = Column(String(60), nullable=False)
    item_purposes = Column(JSON, nullable=False)
    mixed_import_purposes = Column(Boolean, nullable=False, default=False)
    items_count = Column(Integer, nullable=False)
    customs_value = Column(Numeric(18, 2), nullable=False)
    allocated_shared_costs = Column(JSON, nullable=False)
    totals = Column(JSON, nullable=False)
    status = Column(String(20), nullable=False, default="planned", index=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    document_plan = relationship("NfeDocumentPlan", back_populates="documents")
    items = relationship(
        "NfePlannedDocumentItem",
        back_populates="planned_document",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="NfePlannedDocumentItem.duimp_item_number",
    )
    drafts = relationship(
        "NfeDraft",
        back_populates="planned_document",
        lazy=True,
        order_by="NfeDraft.created_at",
    )

    __table_args__ = (
        UniqueConstraint(
            "document_plan_id",
            "exporter_key",
            name="uq_nfe_planned_document_plan_exporter",
        ),
    )


class NfePlannedDocumentItem(Base):
    __tablename__ = "nfe_planned_document_items"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    document_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_document_plans.id"),
        nullable=False,
        index=True,
    )
    planned_document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_planned_documents.id"),
        nullable=False,
        index=True,
    )
    duimp_snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("duimp_snapshots.id"),
        nullable=False,
        index=True,
    )
    item_classification_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_item_classifications.id"),
        nullable=True,
        index=True,
    )
    duimp_item_number = Column(String(30), nullable=False)
    exporter_key = Column(String(255), nullable=False)
    exporter_code = Column(String(100), nullable=True)
    import_purpose = Column(String(30), nullable=False, index=True)
    cfop = Column(String(4), nullable=False)
    customs_value = Column(Numeric(18, 2), nullable=False)
    allocated_shared_costs = Column(JSON, nullable=False)
    raw_source_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    document_plan = relationship("NfeDocumentPlan")
    planned_document = relationship(
        "NfePlannedDocument",
        back_populates="items",
    )
    duimp_snapshot = relationship("DuimpSnapshot")
    item_classification = relationship("NfeItemClassification")

    __table_args__ = (
        UniqueConstraint(
            "document_plan_id",
            "duimp_item_number",
            name="uq_nfe_planned_document_item_plan_item",
        ),
    )


class NfeDraft(Base):
    __tablename__ = "nfe_drafts"

    id = uuid_pk()

    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    import_process_id = Column(UUID(as_uuid=True), ForeignKey("import_processes.id"), nullable=False, index=True)

    importer_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True)
    duimp_snapshot_id = Column(UUID(as_uuid=True), ForeignKey("duimp_snapshots.id"), nullable=True, index=True)
    planned_document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_planned_documents.id"),
        nullable=True,
        index=True,
    )

    model = enum_column(NfeModel, name="nfe_model", default=NfeModel.NFE.value, length=2)
    purpose = enum_column(NfePurpose, name="nfe_purpose", default=NfePurpose.NORMAL.value)
    operation_type = enum_column(
        NfeOperationType,
        name="nfe_operation_type",
        default=NfeOperationType.ENTRY.value,
    )
    environment = enum_column(FiscalEnvironment, name="nfe_environment")

    series = Column(String(10), nullable=False)
    number = Column(Integer, nullable=True)

    status = enum_column(
        NfeDraftStatus,
        name="nfe_draft_status",
        default=NfeDraftStatus.DRAFT.value,
    )

    fiscal_payload = Column(JSON, nullable=False)

    validation_errors = Column(JSON, nullable=True)
    validation_warnings = Column(JSON, nullable=True)

    access_key = Column(String(44), nullable=True)

    # Exclusão sempre lógica. Quando já existe número/chave, deletion_mode é
    # ``archived`` e o payload mantém a indicação de possível inutilização.
    deleted_at = Column(DateTime, nullable=True, index=True)
    deleted_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    deletion_reason = Column(Text, nullable=True)
    deletion_mode = Column(String(20), nullable=True)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    import_process = relationship("ImportProcess", back_populates="nfe_drafts")
    importer = relationship("Client", foreign_keys=[importer_id])
    duimp_snapshot = relationship("DuimpSnapshot")
    planned_document = relationship(
        "NfePlannedDocument",
        back_populates="drafts",
    )
    deleted_by = relationship("User", foreign_keys=[deleted_by_user_id])
    items = relationship("NfeDraftItem", back_populates="nfe_draft", lazy=True, cascade="all, delete-orphan")
    xml_versions = relationship("NfeXmlVersion", back_populates="nfe_draft", lazy=True, cascade="all, delete-orphan")


class NfeDraftItem(Base):
    __tablename__ = "nfe_draft_items"

    id = uuid_pk()

    nfe_draft_id = Column(UUID(as_uuid=True), ForeignKey("nfe_drafts.id"), nullable=False, index=True)

    item_number = Column(Integer, nullable=False)
    duimp_item_number = Column(String(30), nullable=True)

    product_code = Column(String(80), nullable=False)
    description = Column(String(500), nullable=False)

    ncm = Column(String(8), nullable=False)
    cfop = Column(String(4), nullable=False)
    cest = Column(String(10), nullable=True)

    commercial_unit = Column(String(20), nullable=False)
    commercial_quantity = Column(Numeric(18, 4), nullable=False)
    commercial_unit_value = Column(Numeric(18, 10), nullable=False)

    taxable_unit = Column(String(20), nullable=False)
    taxable_quantity = Column(Numeric(18, 4), nullable=False)
    taxable_unit_value = Column(Numeric(18, 10), nullable=False)

    product_value = Column(Numeric(18, 2), nullable=False)
    freight_value = Column(Numeric(18, 2), nullable=False, default=0)
    insurance_value = Column(Numeric(18, 2), nullable=False, default=0)
    discount_value = Column(Numeric(18, 2), nullable=False, default=0)
    other_value = Column(Numeric(18, 2), nullable=False, default=0)

    import_payload = Column(JSON, nullable=True)
    tax_payload = Column(JSON, nullable=True)

    import_purpose = Column(String(30), nullable=True, index=True)
    tax_rule_id = Column(
        UUID(as_uuid=True),
        ForeignKey("client_import_tax_rules.id"),
        nullable=True,
        index=True,
    )
    tax_rule_snapshot = Column(JSON, nullable=True)
    item_classification_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_item_classifications.id"),
        nullable=True,
        index=True,
    )

    raw_source_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    nfe_draft = relationship("NfeDraft", back_populates="items")
    tax_rule = relationship("ClientImportTaxRule", foreign_keys=[tax_rule_id])
    item_classification = relationship(
        "NfeItemClassification",
        foreign_keys=[item_classification_id],
    )

    __table_args__ = (
        UniqueConstraint("nfe_draft_id", "item_number", name="uq_nfe_draft_item_number"),
    )


class NfeXmlVersion(Base):
    __tablename__ = "nfe_xml_versions"

    id = uuid_pk()

    nfe_draft_id = Column(UUID(as_uuid=True), ForeignKey("nfe_drafts.id"), nullable=False, index=True)

    version_number = Column(Integer, nullable=False)
    xml_type = enum_column(NfeXmlType, name="nfe_xml_type")

    xml_content = Column(Text, nullable=False)

    xsd_valid = Column(Boolean, nullable=True)
    xsd_errors = Column(JSON, nullable=True)

    access_key = Column(String(44), nullable=True)
    protocol_number = Column(String(80), nullable=True)

    generated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    generated_at = Column(DateTime, nullable=False)

    nfe_draft = relationship("NfeDraft", back_populates="xml_versions")
    generated_by = relationship("User", foreign_keys=[generated_by_user_id])

    __table_args__ = (
        UniqueConstraint("nfe_draft_id", "version_number", "xml_type", name="uq_nfe_xml_version"),
    )

class NfeNumberSequenceStatusEnum(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class NfeNumberSequence(Base):
    __tablename__ = "nfe_number_sequences"

    id = uuid_pk()

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    client_id = Column(
        UUID(as_uuid=True),
        ForeignKey("clients.id"),
        nullable=False,
        index=True,
    )

    environment = enum_column(
        FiscalEnvironment,
        name="nfe_number_sequence_environment",
        index=True,
    )

    model = Column(String(2), nullable=False, default="55")
    # 55 = NF-e
    # 65 = NFC-e, se futuramente for usado

    series = Column(String(10), nullable=False)
    # Série fiscal da NF-e. Ex: "1", "001", "900"

    current_number = Column(Integer, nullable=False, default=0)
    # Último número reservado/usado

    initial_number = Column(Integer, nullable=False, default=1)
    # Primeiro número permitido para essa sequência

    max_number = Column(Integer, nullable=False, default=999999999)
    # NF-e aceita nNF com até 9 dígitos

    status = enum_column(
        NfeNumberSequenceStatusEnum,
        name="nfe_number_sequence_status",
        default=NfeNumberSequenceStatusEnum.ACTIVE.value,
        index=True,
    )

    last_reserved_number = Column(Integer, nullable=True)
    last_reserved_at = Column(DateTime, nullable=True)

    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "client_id",
            "environment",
            "model",
            "series",
            name="uq_nfe_number_sequence_client_env_model_series",
        ),
    )
