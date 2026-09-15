from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy import Column, ForeignKey, String, Boolean
from sqlalchemy.orm import relationship

from app.models.utils import PasswordMixin, TimestampMixin, uuid_pk

from ..extensions import Base

class User(PasswordMixin, TimestampMixin, Base):
    __tablename__ = "users"

    id = uuid_pk()
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    nome = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)

    role = Column(String(32), nullable=False, default="user")
    setor = Column(String(255), nullable=True)
    ativo = Column(Boolean, nullable=False, default=True)

    organization = relationship("Organization", back_populates="users")
    admin_profile = relationship(
        "AdminProfile",
        uselist=False,
        back_populates="user",
        cascade="all, delete-orphan",
    )

class AdminProfile(TimestampMixin, Base):
    __tablename__ = "admin_profiles"

    id = uuid_pk()
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        unique=True,
        index=True,
    )

    is_super_admin = Column(Boolean, nullable=False, default=False)
    can_manage_users = Column(Boolean, nullable=False, default=True)
    can_manage_settings = Column(Boolean, nullable=False, default=True)
    can_manage_billing = Column(Boolean, nullable=False, default=False)

    user = relationship("User", back_populates="admin_profile")
