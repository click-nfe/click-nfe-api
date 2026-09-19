from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request
from marshmallow import ValidationError

from app.auth import admin_required, auth_required
from app.extensions import db
from app.schemas.fiscal_certificate import (
    RegisterFiscalCertificateSchema,
    UploadFiscalCertificateSchema,
)
from app.services.fiscal_certificate import (
    CertificateMaterial,
    FiscalCertificateError,
    LocalEncryptedFileCertificateVault,
    StoredCertificateReferences,
    certificate_vault_from_config,
)
from app.services.fiscal_certificate_registry import FiscalCertificateRegistry

from .route_helpers import (
    json_payload,
    uuid_or_404,
    validation_error_response,
)


fiscal_certificate_bp = Blueprint(
    "fiscal_certificates",
    __name__,
    url_prefix="/clients",
)
register_schema = RegisterFiscalCertificateSchema()
upload_schema = UploadFiscalCertificateSchema()


def _local_vault() -> LocalEncryptedFileCertificateVault:
    return LocalEncryptedFileCertificateVault(
        root_dir=current_app.config.get(
            "NFE_LOCAL_CERTIFICATE_DIR",
            "/app/data/certificates",
        ),
        encryption_key=current_app.config.get("NFE_LOCAL_CERTIFICATE_KEY"),
        secret_key=current_app.config.get("SECRET_KEY"),
    )


def _service() -> FiscalCertificateRegistry:
    storage_provider = current_app.config.get(
        "NFE_CERTIFICATE_STORAGE_PROVIDER",
        "local_encrypted_file",
    )
    local_vault = _local_vault()
    upload_store = None
    if storage_provider == "local_encrypted_file":
        upload_store = local_vault
    return FiscalCertificateRegistry(
        current_user=g.current_user,
        vault=certificate_vault_from_config(current_app.config),
        upload_store=(
            current_app.config.get("NFE_CERTIFICATE_UPLOAD_STORE")
            or upload_store
        ),
    )


def _error_response(exc: FiscalCertificateError, status_code: int = 400):
    return (
        jsonify(
            {
                "error": "fiscal_certificate_error",
                "message": str(exc),
            }
        ),
        status_code,
    )


@fiscal_certificate_bp.get("/<client_id>/fiscal-certificates")
@auth_required
def list_fiscal_certificates(client_id: str):
    client_uuid = uuid_or_404(client_id)
    try:
        rows = _service().list_for_client(client_uuid)
    except FiscalCertificateError as exc:
        return _error_response(exc, 404)
    return jsonify([_service().public_data(row) for row in rows])


@fiscal_certificate_bp.post("/<client_id>/fiscal-certificates")
@admin_required
def register_fiscal_certificate(client_id: str):
    client_uuid = uuid_or_404(client_id)
    try:
        data = register_schema.load(json_payload())
        row = _service().register(client_id=client_uuid, **data)
        db.session.commit()
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except FiscalCertificateError as exc:
        db.session.rollback()
        return _error_response(exc)
    return jsonify(_service().public_data(row)), 201


@fiscal_certificate_bp.post("/<client_id>/fiscal-certificates/upload")
@admin_required
def upload_fiscal_certificate(client_id: str):
    client_uuid = uuid_or_404(client_id)
    certificate_file = request.files.get("certificate")
    if certificate_file is None or not certificate_file.filename:
        return _error_response(
            FiscalCertificateError("Selecione um certificado .pfx ou .p12."),
        )
    if Path(certificate_file.filename).suffix.lower() not in {".pfx", ".p12"}:
        return _error_response(
            FiscalCertificateError(
                "O certificado deve estar no formato .pfx ou .p12."
            ),
        )

    max_bytes = current_app.config.get(
        "NFE_CERTIFICATE_MAX_BYTES",
        2 * 1024 * 1024,
    )
    certificate_bytes = certificate_file.stream.read(max_bytes + 1)
    if not certificate_bytes:
        return _error_response(
            FiscalCertificateError("O arquivo do certificado está vazio."),
        )
    if len(certificate_bytes) > max_bytes:
        max_megabytes = max_bytes / (1024 * 1024)
        return _error_response(
            FiscalCertificateError(
                "O certificado ultrapassa o limite configurado de "
                f"{max_megabytes:g} MB."
            ),
            413,
        )

    service = None
    stored_references = None
    try:
        data = upload_schema.load(request.form)
        service = _service()
        row = service.upload(
            client_id=client_uuid,
            environment=data["environment"],
            material=CertificateMaterial(
                pkcs12_bytes=certificate_bytes,
                password=data["password"].encode("utf-8"),
            ),
        )
        stored_references = StoredCertificateReferences(
            provider=str(getattr(row.provider, "value", row.provider)),
            certificate_ref=row.certificate_ref,
            password_ref=row.password_ref,
        )
        db.session.commit()
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except FiscalCertificateError as exc:
        db.session.rollback()
        return _error_response(exc, 422)
    except Exception:
        db.session.rollback()
        if service and service.upload_store and stored_references:
            service.upload_store.delete(stored_references)
        raise
    return jsonify({"valid": True, **service.public_data(row)}), 201


@fiscal_certificate_bp.post(
    "/<client_id>/fiscal-certificates/<certificate_id>/validate"
)
@admin_required
def validate_fiscal_certificate(client_id: str, certificate_id: str):
    client_uuid = uuid_or_404(client_id)
    certificate_uuid = uuid_or_404(certificate_id)
    service = _service()
    try:
        row = service.validate(certificate_uuid, client_id=client_uuid)
        db.session.commit()
    except FiscalCertificateError as exc:
        db.session.commit()
        return _error_response(exc, 422)
    return jsonify({"valid": True, **service.public_data(row)})


@fiscal_certificate_bp.post(
    "/<client_id>/fiscal-certificates/<certificate_id>/activate"
)
@admin_required
def activate_fiscal_certificate(client_id: str, certificate_id: str):
    client_uuid = uuid_or_404(client_id)
    certificate_uuid = uuid_or_404(certificate_id)
    service = _service()
    try:
        row = service.activate(certificate_uuid, client_id=client_uuid)
        db.session.commit()
    except FiscalCertificateError as exc:
        db.session.rollback()
        return _error_response(exc, 422)
    return jsonify(service.public_data(row))
