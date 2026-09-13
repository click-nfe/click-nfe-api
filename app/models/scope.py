from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Integer, String, Boolean, Text, Numeric, JSON, Time, UniqueConstraint
from sqlalchemy.orm import relationship

from ..extensions import Base
from app.models.utils import TimestampMixin, uuid_pk

class Scope(TimestampMixin, Base):
    __tablename__ = "scopes"

    id = uuid_pk()

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )

    client_id = Column(
        UUID(as_uuid=True),
        ForeignKey("clients.id"),
        nullable=True,
        index=True,
    )

    status = Column(String(16), nullable=False, default="draft", index=True)

    draft = Column(JSON, nullable=False, default=dict)

    published_snapshot = Column(JSON, nullable=True)

    version = Column(Integer, nullable=True, default=1)

    created_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    responsible_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    last_published_at = Column(DateTime, nullable=True)

    organization = relationship("Organization", back_populates="scopes")
    client = relationship("Client", back_populates="scope")

    created_by = relationship("User", foreign_keys=[created_by_id])
    responsible_user = relationship("User", foreign_keys=[responsible_user_id])

    assignments = relationship(
        "ScopeAssignment",
        back_populates="scope",
        lazy=True,
        cascade="all, delete-orphan",
    )

    services = relationship(
        "ScopeService",
        back_populates="scope",
        lazy=True,
        cascade="all, delete-orphan",
    )

    versions = relationship(
        "ScopeVersion",
        back_populates="scope",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="desc(ScopeVersion.version_number)",
    )

    __table_args__ = (
        UniqueConstraint("client_id", name="uq_scopes_client_id"),
    )


class ScopeVersion(Base):
    __tablename__ = "scope_versions"

    id = uuid_pk()
    scope_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scopes.id"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False)
    draft_snapshot = Column(JSON, nullable=False)
    published_snapshot = Column(JSON, nullable=True)

    created_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    scope = relationship("Scope", back_populates="versions")
    created_by = relationship("User")

    __table_args__ = (
        UniqueConstraint("scope_id", "version_number", name="uq_scope_versions_scope_version"),
    )


class ScopeAssignment(TimestampMixin, Base):
    __tablename__ = "scope_assignments"

    id = uuid_pk()
    scope_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scopes.id"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # Ex.: RESPONSAVEL_COMERCIAL, ANALISTA_DA_IMPORT, ANALISTA_AE_IMPORT, ANALISTA_DA_EXPORT
    role = Column(String(64), nullable=False, index=True)

    active = Column(Boolean, nullable=False, default=True)
    starts_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ends_at = Column(DateTime, nullable=True)

    scope = relationship("Scope", back_populates="assignments")
    user = relationship("User", back_populates="assigned_scopes")

    __table_args__ = (
        Index("ix_scope_assignments_scope_role_active", "scope_id", "role", "active"),
    )


class ServiceCatalog(TimestampMixin, Base):
    __tablename__ = "service_catalog"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    code = Column(String(100), nullable=False, index=True)
    nome = Column(String(255), nullable=False)
    operation_type = Column(String(20), nullable=False)  # IMPORTACAO, EXPORTACAO, AMBOS
    ativo = Column(Boolean, nullable=False, default=True)

    organization = relationship("Organization")
    scope_services = relationship("ScopeService", back_populates="service_catalog", lazy=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_service_catalog_org_code"),
    )


class ScopeService(TimestampMixin, Base):
    __tablename__ = "scope_services"

    id = uuid_pk()
    scope_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scopes.id"),
        nullable=False,
        index=True,
    )
    service_catalog_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_catalog.id"),
        nullable=False,
        index=True,
    )

    enabled = Column(Boolean, nullable=False, default=False)

    # FIXO, OUTRO, PERCENTUAL, CASO_A_CASO
    pricing_type = Column(String(30), nullable=True)
    amount = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(8), nullable=False, default="BRL")

    responsible_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    # detalhes variáveis de cada serviço
    extra_data = Column(JSON, nullable=False, default=dict)

    scope = relationship("Scope", back_populates="services")
    service_catalog = relationship("ServiceCatalog", back_populates="scope_services")
    responsible_user = relationship("User")

    __table_args__ = (
        UniqueConstraint("scope_id", "service_catalog_id", name="uq_scope_service_unique"),
    )

class ScopeTemplate(TimestampMixin, Base):
    __tablename__ = "scope_template"

    id = uuid_pk()

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )

    name = Column(String(128), nullable=False)

    description = Column(String(1024), nullable=True)

    draft = Column(JSON, nullable=False, default=dict)

    created_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    created_by = relationship("User", foreign_keys=[created_by_id])
