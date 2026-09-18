from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Client, ClientFiscalProfile
from app.models.import_process import FiscalEnvironment
from app.models.nfe_issuance import (
    FiscalCertificate,
    FiscalCertificateStatus,
    FiscalCredentialProvider,
)
from app.services.fiscal_certificate import (
    A1CertificateInspector,
    CertificateMaterial,
    CertificateUploadStore,
    CertificateVault,
    DefaultCertificateVault,
    FiscalCertificateError,
)


class FiscalCertificateRegistry:
    def __init__(
        self,
        *,
        current_user: Any,
        vault: CertificateVault | None = None,
        upload_store: CertificateUploadStore | None = None,
        inspector: A1CertificateInspector | None = None,
    ) -> None:
        self.current_user = current_user
        self.organization_id = getattr(current_user, "organization_id", None)
        self.vault = vault or DefaultCertificateVault()
        self.upload_store = upload_store
        self.inspector = inspector or A1CertificateInspector()

    def list_for_client(self, client_id) -> list[FiscalCertificate]:
        self._client(client_id)
        return (
            self._query()
            .filter(FiscalCertificate.client_id == client_id)
            .order_by(
                FiscalCertificate.environment.asc(),
                FiscalCertificate.created_at.desc(),
            )
            .all()
        )

    def register(
        self,
        *,
        client_id,
        environment: str,
        provider: str,
        certificate_ref: str,
        password_ref: str,
    ) -> FiscalCertificate:
        self._client(client_id)
        profile = self._fiscal_profile(client_id)
        environment_value = self._enum_value(
            FiscalEnvironment,
            environment,
            "Ambiente fiscal inválido.",
        )
        provider_value = self._enum_value(
            FiscalCredentialProvider,
            provider,
            "Provider do certificado inválido.",
        )
        certificate_ref = str(certificate_ref or "").strip()
        password_ref = str(password_ref or "").strip()
        if not certificate_ref or not password_ref:
            raise FiscalCertificateError(
                "As referências do certificado e da senha são obrigatórias."
            )
        if self._query().filter(
            FiscalCertificate.certificate_ref == certificate_ref
        ).first():
            raise FiscalCertificateError(
                "A referência do certificado já está cadastrada."
            )

        now = datetime.utcnow()
        row = FiscalCertificate(
            organization_id=self.organization_id,
            client_id=client_id,
            environment=environment_value,
            provider=provider_value,
            status=FiscalCertificateStatus.PENDING_VALIDATION.value,
            certificate_ref=certificate_ref,
            password_ref=password_ref,
            issuer_cnpj=self._digits(profile.cnpj),
            is_active=False,
            created_by_user_id=getattr(self.current_user, "id", None),
            created_at=now,
            updated_at=now,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def upload(
        self,
        *,
        client_id,
        environment: str,
        material: CertificateMaterial,
    ) -> FiscalCertificate:
        self._client(client_id)
        profile = self._fiscal_profile(client_id)
        environment_value = self._enum_value(
            FiscalEnvironment,
            environment,
            "Ambiente fiscal inválido.",
        )
        if self.upload_store is None:
            raise FiscalCertificateError(
                "O armazenamento de upload de certificados não está configurado."
            )

        loaded = self.inspector.load(
            material,
            expected_cnpj=profile.cnpj,
        )
        if self._query().filter(
            FiscalCertificate.client_id == client_id,
            FiscalCertificate.certificate_fingerprint_sha256
            == loaded.fingerprint_sha256,
        ).first():
            raise FiscalCertificateError(
                "Este certificado A1 já está cadastrado para o cliente."
            )

        references = self.upload_store.store(
            organization_id=str(self._require_organization_id()),
            client_id=str(client_id),
            material=material,
        )
        try:
            row = self.register(
                client_id=client_id,
                environment=environment_value,
                provider=references.provider,
                certificate_ref=references.certificate_ref,
                password_ref=references.password_ref,
            )
            self._apply_metadata(row, loaded)
            row.status = FiscalCertificateStatus.PENDING_VALIDATION.value
            row.is_active = False
            db.session.flush()
            return row
        except IntegrityError as exc:
            self.upload_store.delete(references)
            raise FiscalCertificateError(
                "Este certificado A1 já está cadastrado para o cliente."
            ) from exc
        except Exception:
            self.upload_store.delete(references)
            raise

    def validate(self, certificate_id, *, client_id) -> FiscalCertificate:
        row = self.get(certificate_id, client_id=client_id)
        try:
            loaded = self._load(row)
        except FiscalCertificateError as exc:
            row.status = FiscalCertificateStatus.INVALID.value
            row.is_active = False
            row.validation_error = str(exc)
            row.last_validated_at = datetime.utcnow()
            row.updated_at = row.last_validated_at
            db.session.flush()
            raise

        self._apply_metadata(row, loaded)
        row.status = FiscalCertificateStatus.PENDING_VALIDATION.value
        row.is_active = False
        db.session.flush()
        return row

    def activate(self, certificate_id, *, client_id) -> FiscalCertificate:
        row = self.get(certificate_id, client_id=client_id)
        loaded = self._load(row)
        self._apply_metadata(row, loaded)
        now = datetime.utcnow()

        active_rows = self._query().filter(
            FiscalCertificate.client_id == client_id,
            FiscalCertificate.environment == row.environment,
            FiscalCertificate.id != row.id,
            FiscalCertificate.is_active.is_(True),
        ).with_for_update()
        for active in active_rows.all():
            active.is_active = False
            active.status = FiscalCertificateStatus.DISABLED.value
            active.updated_at = now

        row.status = FiscalCertificateStatus.ACTIVE.value
        row.is_active = True
        row.updated_at = now
        db.session.flush()
        return row

    def get(self, certificate_id, *, client_id) -> FiscalCertificate:
        row = (
            self._query()
            .filter(
                FiscalCertificate.id == certificate_id,
                FiscalCertificate.client_id == client_id,
            )
            .first()
        )
        if not row:
            raise FiscalCertificateError(
                "Certificado fiscal não encontrado."
            )
        return row

    def active_for(
        self,
        *,
        client_id,
        environment: str,
        certificate_id=None,
    ) -> FiscalCertificate:
        query = self._query().filter(
            FiscalCertificate.client_id == client_id,
            FiscalCertificate.environment == environment,
            FiscalCertificate.status
            == FiscalCertificateStatus.ACTIVE.value,
            FiscalCertificate.is_active.is_(True),
        )
        if certificate_id is not None:
            query = query.filter(FiscalCertificate.id == certificate_id)
        rows = query.all()
        if not rows:
            raise FiscalCertificateError(
                "Não existe certificado A1 ativo para o cliente e ambiente."
            )
        if len(rows) > 1:
            raise FiscalCertificateError(
                "Existe mais de um certificado A1 ativo para o mesmo ambiente."
            )
        return rows[0]

    def load_active(self, row: FiscalCertificate):
        loaded = self._load(row)
        self._apply_metadata(row, loaded)
        db.session.flush()
        return loaded

    def apply_loaded_metadata(self, row: FiscalCertificate, loaded) -> None:
        self._apply_metadata(row, loaded)
        db.session.flush()

    def _load(self, row: FiscalCertificate):
        provider = getattr(row.provider, "value", row.provider)
        material = self.vault.resolve(
            provider=str(provider),
            certificate_ref=row.certificate_ref,
            password_ref=row.password_ref,
        )
        return self.inspector.load(
            material,
            expected_cnpj=row.issuer_cnpj,
        )

    def _apply_metadata(self, row: FiscalCertificate, loaded) -> None:
        now = datetime.utcnow()
        row.certificate_fingerprint_sha256 = loaded.fingerprint_sha256
        row.certificate_serial_number = loaded.serial_number
        row.subject_name = loaded.subject_name
        row.valid_from = loaded.valid_from.replace(tzinfo=None)
        row.valid_until = loaded.valid_until.replace(tzinfo=None)
        row.last_validated_at = now
        row.validation_error = None
        row.updated_at = now

    def _query(self):
        return FiscalCertificate.query.filter(
            FiscalCertificate.organization_id == self._require_organization_id()
        )

    def _client(self, client_id) -> Client:
        query = Client.query.filter(
            Client.id == client_id,
            Client.organization_id == self._require_organization_id(),
        )
        client = query.first()
        if not client:
            raise FiscalCertificateError("Cliente não encontrado.")
        return client

    def _fiscal_profile(self, client_id) -> ClientFiscalProfile:
        query = ClientFiscalProfile.query.filter(
            ClientFiscalProfile.client_id == client_id,
            ClientFiscalProfile.is_default.is_(True),
        )
        query = query.filter(
            ClientFiscalProfile.organization_id == self._require_organization_id()
        )
        profile = query.first()
        if not profile:
            raise FiscalCertificateError(
                "O cliente não possui perfil fiscal padrão."
            )
        return profile

    def _require_organization_id(self):
        if not self.organization_id:
            raise FiscalCertificateError(
                "O usuário precisa estar vinculado a uma organização."
            )
        return self.organization_id

    @staticmethod
    def public_data(row: FiscalCertificate) -> dict[str, Any]:
        def value(item):
            return getattr(item, "value", item)

        return {
            "id": str(row.id),
            "organization_id": str(row.organization_id),
            "client_id": str(row.client_id),
            "environment": value(row.environment),
            "provider": value(row.provider),
            "status": value(row.status),
            "issuer_cnpj": row.issuer_cnpj,
            "certificate_fingerprint_sha256": (
                row.certificate_fingerprint_sha256
            ),
            "certificate_serial_number": row.certificate_serial_number,
            "subject_name": row.subject_name,
            "valid_from": (
                row.valid_from.isoformat() if row.valid_from else None
            ),
            "valid_until": (
                row.valid_until.isoformat() if row.valid_until else None
            ),
            "is_active": row.is_active,
            "last_validated_at": (
                row.last_validated_at.isoformat()
                if row.last_validated_at
                else None
            ),
            "validation_error": row.validation_error,
            "created_at": (
                row.created_at.isoformat() if row.created_at else None
            ),
            "updated_at": (
                row.updated_at.isoformat() if row.updated_at else None
            ),
        }

    @staticmethod
    def _enum_value(enum_class, raw_value: str, message: str) -> str:
        try:
            return enum_class(str(raw_value)).value
        except ValueError as exc:
            raise FiscalCertificateError(message) from exc

    @staticmethod
    def _digits(value: str) -> str:
        return "".join(
            character
            for character in str(value or "")
            if character.isdigit()
        )
