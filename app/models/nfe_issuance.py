from __future__ import annotations

from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.import_process import FiscalEnvironment, enum_column
from app.models.utils import uuid_pk

from ..extensions import Base


class FiscalCredentialProvider(str, Enum):
    LOCAL_ENCRYPTED_FILE = "local_encrypted_file"
    GCP_SECRET_MANAGER = "gcp_secret_manager"
    GCP_CLOUD_STORAGE = "gcp_cloud_storage"


class FiscalCertificateStatus(str, Enum):
    PENDING_VALIDATION = "pending_validation"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DISABLED = "disabled"
    INVALID = "invalid"


class NfeAttemptOperation(str, Enum):
    XSD_VALIDATION = "xsd_validation"
    SIGNATURE = "signature"
    SERVICE_STATUS = "service_status"
    AUTHORIZATION = "authorization"
    RECEIPT_QUERY = "receipt_query"
    PROTOCOL_QUERY = "protocol_query"
    CANCELLATION = "cancellation"


class NfeAttemptStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class NfeProtocolType(str, Enum):
    AUTHORIZATION = "authorization"
    CANCELLATION = "cancellation"


class FiscalCertificate(Base):
    __tablename__ = "fiscal_certificates"

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
        name="fiscal_certificate_environment",
    )
    provider = enum_column(
        FiscalCredentialProvider,
        name="fiscal_credential_provider",
    )
    status = enum_column(
        FiscalCertificateStatus,
        name="fiscal_certificate_status",
        default=FiscalCertificateStatus.PENDING_VALIDATION.value,
    )

    certificate_ref = Column(String(500), nullable=False)
    password_ref = Column(String(500), nullable=False)
    issuer_cnpj = Column(String(14), nullable=False, index=True)
    certificate_fingerprint_sha256 = Column(String(64), nullable=True)
    certificate_serial_number = Column(String(160), nullable=True)
    subject_name = Column(String(500), nullable=True)
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    last_validated_at = Column(DateTime, nullable=True)
    validation_error = Column(Text, nullable=True)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    client = relationship("Client")
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "certificate_ref",
            name="uq_fiscal_certificate_org_ref",
        ),
        UniqueConstraint(
            "organization_id",
            "client_id",
            "certificate_fingerprint_sha256",
            name="uq_fiscal_certificate_client_fingerprint",
        ),
    )


class NfeIssuance(Base):
    __tablename__ = "nfe_issuances"

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
    nfe_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_drafts.id"),
        nullable=False,
        index=True,
    )
    importer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("clients.id"),
        nullable=False,
        index=True,
    )
    certificate_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fiscal_certificates.id"),
        nullable=True,
        index=True,
    )

    environment = enum_column(
        FiscalEnvironment,
        name="nfe_issuance_environment",
    )
    status = Column(String(40), nullable=False, default="draft", index=True)
    model = Column(String(2), nullable=False, default="55")
    series = Column(String(3), nullable=False)
    number = Column(Integer, nullable=False)
    access_key = Column(String(44), nullable=True, unique=True, index=True)

    idempotency_key = Column(String(128), nullable=False)
    request_hash = Column(String(64), nullable=False)
    lock_version = Column(Integer, nullable=False, default=0)

    sefaz_authorizer = Column(String(30), nullable=True)
    receipt_number = Column(String(80), nullable=True, index=True)
    protocol_number = Column(String(80), nullable=True, index=True)
    rejection_code = Column(String(20), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    last_error_code = Column(String(100), nullable=True)
    last_error_message = Column(Text, nullable=True)

    submitted_at = Column(DateTime, nullable=True)
    authorized_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    organization = relationship("Organization")
    import_process = relationship("ImportProcess")
    nfe_draft = relationship("NfeDraft")
    importer = relationship("Client")
    certificate = relationship("FiscalCertificate")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    attempts = relationship(
        "NfeIssuanceAttempt",
        back_populates="issuance",
        cascade="all, delete-orphan",
    )
    events = relationship(
        "NfeIssuanceEvent",
        back_populates="issuance",
        cascade="all, delete-orphan",
    )
    protocols = relationship(
        "NfeProtocol",
        back_populates="issuance",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_nfe_issuance_org_idempotency",
        ),
        UniqueConstraint(
            "organization_id",
            "importer_id",
            "environment",
            "model",
            "series",
            "number",
            name="uq_nfe_issuance_fiscal_number",
        ),
        CheckConstraint(
            "status IN ("
            "'draft', 'validated', 'xml_generated', 'xsd_validated', "
            "'validation_failed', 'signed', 'submission_pending', 'submitted', "
            "'processing', 'authorized', 'rejected', 'denied', "
            "'cancellation_pending', 'cancelled', 'failed'"
            ")",
            name="ck_nfe_issuance_status",
        ),
        CheckConstraint("lock_version >= 0", name="ck_nfe_issuance_lock_version"),
    )


class NfeIssuanceAttempt(Base):
    __tablename__ = "nfe_issuance_attempts"

    id = uuid_pk()
    nfe_issuance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_issuances.id"),
        nullable=False,
        index=True,
    )
    attempt_number = Column(Integer, nullable=False)
    operation = enum_column(
        NfeAttemptOperation,
        name="nfe_attempt_operation",
    )
    status = enum_column(
        NfeAttemptStatus,
        name="nfe_attempt_status",
        default=NfeAttemptStatus.STARTED.value,
    )
    endpoint = Column(String(500), nullable=True)
    correlation_id = Column(String(100), nullable=True, index=True)
    request_checksum = Column(String(64), nullable=True)
    response_checksum = Column(String(64), nullable=True)
    response_code = Column(String(30), nullable=True)
    response_message = Column(Text, nullable=True)
    receipt_number = Column(String(80), nullable=True)
    protocol_number = Column(String(80), nullable=True)
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    issuance = relationship("NfeIssuance", back_populates="attempts")

    __table_args__ = (
        UniqueConstraint(
            "nfe_issuance_id",
            "operation",
            "attempt_number",
            name="uq_nfe_attempt_operation_number",
        ),
        CheckConstraint("attempt_number > 0", name="ck_nfe_attempt_positive"),
    )


class NfeIssuanceEvent(Base):
    __tablename__ = "nfe_issuance_events"

    id = uuid_pk()
    nfe_issuance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_issuances.id"),
        nullable=False,
        index=True,
    )
    previous_status = Column(String(40), nullable=False)
    current_status = Column(String(40), nullable=False)
    reason = Column(String(500), nullable=True)
    event_metadata = Column(JSON, nullable=True)
    actor_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, index=True)

    issuance = relationship("NfeIssuance", back_populates="events")
    actor = relationship("User", foreign_keys=[actor_user_id])


class NfeProtocol(Base):
    __tablename__ = "nfe_protocols"

    id = uuid_pk()
    nfe_issuance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nfe_issuances.id"),
        nullable=False,
        index=True,
    )
    protocol_type = enum_column(
        NfeProtocolType,
        name="nfe_protocol_type",
    )
    event_sequence = Column(Integer, nullable=False, default=1)
    status_code = Column(String(20), nullable=False)
    status_message = Column(Text, nullable=True)
    protocol_number = Column(String(80), nullable=True, index=True)
    response_xml = Column(Text, nullable=False)
    response_checksum_sha256 = Column(String(64), nullable=False)
    received_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)

    issuance = relationship("NfeIssuance", back_populates="protocols")

    __table_args__ = (
        UniqueConstraint(
            "nfe_issuance_id",
            "protocol_type",
            "event_sequence",
            name="uq_nfe_protocol_type_sequence",
        ),
        CheckConstraint("event_sequence > 0", name="ck_nfe_protocol_sequence"),
    )
