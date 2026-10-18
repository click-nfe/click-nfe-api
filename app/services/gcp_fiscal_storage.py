"""Persistent A1 storage: private Cloud Storage object and versioned password secret."""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from typing import Any, Mapping

from .fiscal_certificate import (
    CertificateMaterial,
    FiscalCertificateError,
    StoredCertificateReferences,
)


class GcpCloudStorageCertificateVault:
    provider = "gcp_cloud_storage"
    _SECRET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
    _CERTIFICATE_REF = re.compile(
        r"^gcs:([^/]+)/([0-9a-f-]{36})/([0-9a-f-]{36})/([0-9a-f]{32})\.pfx#([1-9][0-9]*)$"
    )

    def __init__(
        self,
        *,
        bucket_name: str | None,
        project_id: str | None,
        password_secret_name: str = "click-nfe-a1-passwords",
        storage_client: Any | None = None,
        secret_client: Any | None = None,
    ) -> None:
        self.bucket_name = bucket_name
        self.project_id = project_id
        self.password_secret_name = password_secret_name
        self._storage_client = storage_client
        self._secret_client = secret_client

    @classmethod
    def from_config(cls, config: Mapping[str, Any]):
        return cls(
            bucket_name=config.get("NFE_CERTIFICATE_BUCKET"),
            project_id=(config.get("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
                        or os.getenv("GCP_PROJECT_ID")),
            password_secret_name=config.get(
                "NFE_CERTIFICATE_PASSWORD_SECRET_NAME", "click-nfe-a1-passwords"
            ),
        )

    def _require_configuration(self) -> None:
        if not self.bucket_name or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", self.bucket_name):
            raise FiscalCertificateError("NFE_CERTIFICATE_BUCKET inválido ou ausente.")
        if not self.project_id or not self._SECRET_NAME.fullmatch(self.password_secret_name):
            raise FiscalCertificateError("Projeto ou secret de senhas A1 inválido ou ausente.")

    def _storage(self):
        if self._storage_client is None:
            from google.cloud import storage

            self._storage_client = storage.Client(project=self.project_id)
        return self._storage_client

    def _secrets(self):
        if self._secret_client is None:
            from google.cloud import secretmanager

            self._secret_client = secretmanager.SecretManagerServiceClient()
        return self._secret_client

    def _secret_parent(self) -> str:
        return f"projects/{self.project_id}/secrets/{self.password_secret_name}"

    def store(
        self, *, organization_id: str, client_id: str, material: CertificateMaterial
    ) -> StoredCertificateReferences:
        self._require_configuration()
        try:
            organization = str(uuid.UUID(organization_id))
            client = str(uuid.UUID(client_id))
        except ValueError as exc:
            raise FiscalCertificateError("Organização ou cliente inválido para o certificado.") from exc
        token = uuid.uuid4().hex
        key = f"{organization}/{client}/{token}.pfx"
        blob = self._storage().bucket(self.bucket_name).blob(key)
        version_name = None
        try:
            # A generation precondition prevents overwriting an existing A1.
            blob.upload_from_string(
                material.pkcs12_bytes,
                content_type="application/x-pkcs12",
                if_generation_match=0,
            )
            generation = int(blob.generation)
            payload = json.dumps({
                "organization_id": organization,
                "client_id": client,
                "token": token,
                "password": base64.b64encode(material.password).decode("ascii"),
            }, separators=(",", ":")).encode("utf-8")
            if len(payload) > 65536:
                raise FiscalCertificateError("A senha do certificado excede o limite do Secret Manager.")
            version = self._secrets().add_secret_version(
                request={"parent": self._secret_parent(), "payload": {"data": payload}}
            )
            version_name = version.name
            version_number = int(version_name.rsplit("/", 1)[-1])
            return StoredCertificateReferences(
                provider=self.provider,
                certificate_ref=f"gcs:{self.bucket_name}/{key}#{generation}",
                password_ref=f"gcp:{self.password_secret_name}@{version_number}",
            )
        except Exception as exc:
            if version_name:
                try:
                    self._secrets().destroy_secret_version(request={"name": version_name})
                except Exception:
                    pass
            try:
                if blob.generation:
                    blob.delete(if_generation_match=int(blob.generation))
            except Exception:
                pass
            if isinstance(exc, FiscalCertificateError):
                raise
            raise FiscalCertificateError("Não foi possível armazenar o certificado A1.") from None

    def _reference(self, certificate_ref: str, password_ref: str):
        self._require_configuration()
        match = self._CERTIFICATE_REF.fullmatch(str(certificate_ref or ""))
        if not match or match.group(1) != self.bucket_name:
            raise FiscalCertificateError("Referência do certificado no Cloud Storage inválida.")
        organization, client = match.group(2), match.group(3)
        try:
            if str(uuid.UUID(organization)) != organization or str(uuid.UUID(client)) != client:
                raise ValueError
        except ValueError as exc:
            raise FiscalCertificateError("Referência do certificado no Cloud Storage inválida.") from exc
        secret_match = re.fullmatch(
            rf"gcp:{re.escape(self.password_secret_name)}@([1-9][0-9]*)",
            str(password_ref or ""),
        )
        if not secret_match:
            raise FiscalCertificateError("Referência da senha A1 inválida.")
        key = f"{organization}/{client}/{match.group(4)}.pfx"
        return key, int(match.group(5)), match.group(4), organization, client, secret_match.group(1)

    def resolve(
        self, *, provider: str, certificate_ref: str, password_ref: str
    ) -> CertificateMaterial:
        if provider not in {self.provider, "FiscalCredentialProvider.GCP_CLOUD_STORAGE"}:
            raise FiscalCertificateError("Provider do certificado incompatível.")
        key, generation, token, organization, client, version = self._reference(
            certificate_ref, password_ref
        )
        try:
            blob = self._storage().bucket(self.bucket_name).blob(key, generation=generation)
            certificate = blob.download_as_bytes()
            response = self._secrets().access_secret_version(request={
                "name": f"{self._secret_parent()}/versions/{version}"
            })
            payload = json.loads(bytes(response.payload.data))
            if (payload.get("organization_id"), payload.get("client_id"), payload.get("token")) != (
                organization, client, token
            ):
                raise ValueError("mismatch")
            password = base64.b64decode(payload["password"], validate=True)
            if not certificate or not password:
                raise ValueError("empty material")
            return CertificateMaterial(pkcs12_bytes=certificate, password=password)
        except Exception:
            raise FiscalCertificateError("Não foi possível recuperar o certificado A1 persistido.") from None

    def delete(self, references: StoredCertificateReferences) -> None:
        key, generation, *_rest, version = self._reference(
            references.certificate_ref, references.password_ref
        )
        try:
            self._secrets().destroy_secret_version(request={
                "name": f"{self._secret_parent()}/versions/{version}"
            })
        except Exception:
            pass
        try:
            self._storage().bucket(self.bucket_name).blob(key).delete(
                if_generation_match=generation
            )
        except Exception:
            pass
