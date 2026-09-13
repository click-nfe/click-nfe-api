
from datetime import datetime

from app.models.utils import TimestampMixin, uuid_pk

from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy import Column, DateTime, ForeignKey, String, Boolean, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from ..extensions import Base

class Client(TimestampMixin, Base):
    __tablename__ = "clients"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    cnpj = Column(String(14), nullable=False, index=True)
    razao_social = Column(String(255), nullable=False, index=True)
    nome_resumido = Column(String(255), nullable=True)

    inscricao_estadual = Column(String(64), nullable=True)
    inscricao_municipal = Column(String(64), nullable=True)

    endereco_completo_escritorio = Column(Text, nullable=True)
    endereco_completo_armazem = Column(Text, nullable=True)

    cnae_principal = Column(Text, nullable=True)
    cnae_secundario = Column(Text, nullable=True)
    regime_tributacao = Column(String(32), nullable=True)

    ativo = Column(Boolean, nullable=False, default=True)

    organization = relationship("Organization", back_populates="clients")

    contatos = relationship(
        "ClientContact",
        back_populates="client",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="desc(ClientContact.principal), ClientContact.nome.asc()",
    )

    scope = relationship(
        "Scope",
        back_populates="client",
        lazy=True,
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "cnpj", name="uq_clients_org_cnpj"),
    )

class ClientContact(TimestampMixin, Base):
    __tablename__ = "client_contacts"

    id = uuid_pk()
    client_id = Column(
        UUID(as_uuid=True),
        ForeignKey("clients.id"),
        nullable=False,
        index=True,
    )

    nome = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True, index=True)
    telefone = Column(String(64), nullable=True)
    whatsapp = Column(String(64), nullable=True)
    cargo_departamento = Column(String(255), nullable=True)
    principal = Column(Boolean, nullable=False, default=False)
    ativo = Column(Boolean, nullable=False, default=True)

    client = relationship("Client", back_populates="contatos")
    
class ClientFiscalProfile(Base):
    __tablename__ = "client_fiscal_profiles"

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

    legal_name = Column(String(255), nullable=False)
    trade_name = Column(String(255), nullable=True)

    cnpj = Column(String(14), nullable=False)
    state_registration = Column(String(30), nullable=True)

    tax_regime = Column(String(1), nullable=False)
    # 1 = Simples Nacional
    # 2 = Simples Nacional - excesso sublimite
    # 3 = Regime Normal

    street = Column(String(255), nullable=False)
    number = Column(String(60), nullable=False)
    complement = Column(String(255), nullable=True)
    district = Column(String(120), nullable=False)

    city_code = Column(String(7), nullable=False)
    city_name = Column(String(120), nullable=False)
    state = Column(String(2), nullable=False)
    zip_code = Column(String(8), nullable=False)

    country_code = Column(String(4), nullable=False, default="1058")
    country_name = Column(String(60), nullable=False, default="Brasil")

    phone = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)

    is_default = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "client_id",
            "is_default",
            name="uq_client_fiscal_profile_default",
        ),
    )
