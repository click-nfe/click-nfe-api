
from sqlalchemy import Boolean, Column, String
from sqlalchemy.orm import relationship

from app.models.utils import TimestampMixin, uuid_pk

from ..extensions import Base

class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id = uuid_pk()
    nome = Column(String(255), nullable=False, unique=True, index=True)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    cnpj = Column(String(14), nullable=True, unique=True, index=True)
    email = Column(String(255), nullable=True)
    telefone = Column(String(64), nullable=True)
    ativo = Column(Boolean, nullable=False, default=True)

    users = relationship("User", back_populates="organization", lazy=True)
    clients = relationship("Client", back_populates="organization", lazy=True)
