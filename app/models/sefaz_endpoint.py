"""Production NF-e authorizers by issuing state (cUF)."""

from sqlalchemy import Boolean, Column, DateTime, String

from ..extensions import Base


class SefazEndpoint(Base):
    __tablename__ = "sefaz_endpoints"

    cuf = Column(String(2), primary_key=True)
    uf = Column(String(2), nullable=False, unique=True)
    authorizer = Column(String(8), nullable=False)
    authorization_url = Column(String(500), nullable=False)
    receipt_url = Column(String(500), nullable=False)
    protocol_url = Column(String(500), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False)
