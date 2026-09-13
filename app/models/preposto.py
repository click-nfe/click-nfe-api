from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy import Boolean, Column, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.utils import TimestampMixin, uuid_pk

from ..extensions import Base

class Preposto(TimestampMixin, Base):
    __tablename__ = "prepostos"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )

    nome = Column(String(255), nullable=False, index=True)
    razao_social = Column(String(255), nullable=True)
    ativo = Column(Boolean, nullable=False, default=True)
    observacoes = Column(Text, nullable=True)

    organization = relationship("Organization", back_populates="prepostos")

    contatos = relationship(
        "PrepostoContato",
        back_populates="preposto",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="desc(PrepostoContato.principal), PrepostoContato.nome.asc()",
    )

    localidades = relationship(
        "PrepostoLocalidade",
        back_populates="preposto",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="PrepostoLocalidade.cidade.asc()",
    )

    scope_links = relationship(
        "ScopePreposto",
        back_populates="preposto",
        lazy=True,
        cascade="all, delete-orphan",
    )

    credenciado_links = relationship(
        "PrepostoCredenciadoVinculo",
        back_populates="preposto",
        lazy=True,
        cascade="all, delete-orphan",
    )


class PrepostoContato(TimestampMixin, Base):
    __tablename__ = "preposto_contatos"

    id = uuid_pk()
    preposto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("prepostos.id"),
        nullable=False,
        index=True,
    )

    nome = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True, index=True)
    telefone = Column(String(64), nullable=True)
    whatsapp = Column(String(64), nullable=True)
    principal = Column(Boolean, nullable=False, default=False)

    preposto = relationship("Preposto", back_populates="contatos")


class PrepostoLocalidade(TimestampMixin, Base):
    __tablename__ = "preposto_localidades"

    id = uuid_pk()
    preposto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("prepostos.id"),
        nullable=False,
        index=True,
    )

    cidade = Column(String(255), nullable=False, index=True)
    uf = Column(String(2), nullable=True, index=True)
    descricao_local = Column(String(255), nullable=True)
    tipo_local = Column(String(32), nullable=True)  # CIDADE, PORTO, AEROPORTO, CLIA, FRONTEIRA

    atende_importacao = Column(Boolean, nullable=False, default=False)
    atende_exportacao = Column(Boolean, nullable=False, default=False)

    valor_importacao = Column(Numeric(12, 2), nullable=True)
    valor_exportacao = Column(Numeric(12, 2), nullable=True)

    valor_importacao_descricao = Column(String(255), nullable=True)
    valor_exportacao_descricao = Column(String(255), nullable=True)

    moeda = Column(String(8), nullable=False, default="BRL")
    observacoes = Column(Text, nullable=True)

    preposto = relationship("Preposto", back_populates="localidades")

    tarifas = relationship(
        "PrepostoTarifa",
        back_populates="localidade",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="desc(PrepostoTarifa.principal), PrepostoTarifa.condicao.asc()",
    )

    credenciado_links = relationship(
        "PrepostoCredenciadoVinculo",
        back_populates="localidade",
        lazy=True,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_preposto_localidades_cidade_uf", "cidade", "uf"),
        Index(
            "ix_preposto_localidades_operacao",
            "cidade",
            "atende_importacao",
            "atende_exportacao",
        ),
    )


class PrepostoTarifa(TimestampMixin, Base):
    __tablename__ = "preposto_tarifas"

    id = uuid_pk()
    localidade_id = Column(
        UUID(as_uuid=True),
        ForeignKey("preposto_localidades.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    codigo = Column(String(64), nullable=False)
    operacao = Column(String(20), nullable=False, index=True)
    tipo = Column(String(64), nullable=False)
    valor = Column(Numeric(12, 2), nullable=True)
    valor_descricao = Column(String(255), nullable=True)
    condicao = Column(String(500), nullable=True)
    principal = Column(Boolean, nullable=False, default=False)
    moeda = Column(String(8), nullable=False, default="BRL")
    ativo = Column(Boolean, nullable=False, default=True)
    observacoes = Column(Text, nullable=True)

    localidade = relationship("PrepostoLocalidade", back_populates="tarifas")

    __table_args__ = (
        UniqueConstraint("localidade_id", "codigo", name="uq_preposto_tarifa_localidade_codigo"),
        Index("ix_preposto_tarifas_operacao_ativo", "operacao", "ativo"),
    )


class PrepostoCredenciado(TimestampMixin, Base):
    __tablename__ = "preposto_credenciados"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    nome = Column(String(255), nullable=False, index=True)
    cpf = Column(String(11), nullable=False)
    registro_rfb = Column(String(32), nullable=True)
    categoria = Column(String(20), nullable=False, default="DESPACHANTE")
    ativo = Column(Boolean, nullable=False, default=True)
    observacoes = Column(Text, nullable=True)

    vinculos = relationship(
        "PrepostoCredenciadoVinculo",
        back_populates="credenciado",
        lazy=True,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "cpf",
            name="uq_preposto_credenciado_organization_cpf",
        ),
    )


class PrepostoCredenciadoVinculo(TimestampMixin, Base):
    __tablename__ = "preposto_credenciado_vinculos"

    id = uuid_pk()
    credenciado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("preposto_credenciados.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    preposto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("prepostos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    localidade_id = Column(
        UUID(as_uuid=True),
        ForeignKey("preposto_localidades.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ativo = Column(Boolean, nullable=False, default=True)
    observacoes = Column(Text, nullable=True)

    credenciado = relationship("PrepostoCredenciado", back_populates="vinculos")
    preposto = relationship("Preposto", back_populates="credenciado_links")
    localidade = relationship("PrepostoLocalidade", back_populates="credenciado_links")

    __table_args__ = (
        UniqueConstraint(
            "credenciado_id",
            "preposto_id",
            "localidade_id",
            name="uq_preposto_credenciado_vinculo",
        ),
    )


class ScopePreposto(TimestampMixin, Base):
    __tablename__ = "scope_prepostos"

    id = uuid_pk()
    scope_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scopes.id"),
        nullable=False,
        index=True,
    )
    preposto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("prepostos.id"),
        nullable=False,
        index=True,
    )

    operation_type = Column(String(20), nullable=False)  # IMPORTACAO / EXPORTACAO
    enabled = Column(Boolean, nullable=False, default=False)

    valor = Column(Numeric(12, 2), nullable=True)
    incluso_no_desembaraco_casco = Column(Boolean, nullable=True)

    extra_data = Column(JSON, nullable=False, default=dict)

    scope = relationship("Scope")
    preposto = relationship("Preposto", back_populates="scope_links")

    __table_args__ = (
        UniqueConstraint("scope_id", "preposto_id", "operation_type", name="uq_scope_preposto_unique"),
    )
