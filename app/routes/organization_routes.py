from flask import Blueprint, current_app, g, jsonify
from sqlalchemy import func
from marshmallow import ValidationError

from ..auth import admin_required, auth_required
from ..cnpj import is_valid_cnpj, normalize_cnpj
from ..extensions import db
from ..integrations.portal_unico import (
    PortalUnicoIntegrationError,
    portal_credential_resolver_from_config,
    portal_credential_store_from_config,
)
from ..models import Organization
from ..schemas import OrganizationSchema
from ..schemas.portal_unico_settings import ConfigurePortalUnicoSchema
from ..services.portal_unico_settings import (
    PortalUnicoHealthcheckError,
    PortalUnicoSettingsError,
    PortalUnicoSettingsService,
)
from .route_helpers import json_payload, validation_error_response

organization_bp = Blueprint("organizations", __name__, url_prefix="/organizations")
organization_schema = OrganizationSchema()
portal_unico_schema = ConfigurePortalUnicoSchema()


def _portal_unico_service() -> PortalUnicoSettingsService:
    return PortalUnicoSettingsService(
        current_user=g.current_user,
        credential_store=portal_credential_store_from_config(
            current_app.config
        ),
        credential_resolver=portal_credential_resolver_from_config(
            current_app.config
        ),
        gateway_factory=current_app.config.get(
            "PORTAL_UNICO_GATEWAY_FACTORY"
        ),
        timeout_seconds=float(
            current_app.config.get("PORTAL_UNICO_TIMEOUT_SECONDS", 30)
        ),
    )


def _portal_error(exc: Exception, status_code: int = 400):
    return (
        jsonify(
            {
                "error": "portal_unico_settings_error",
                "message": str(exc),
            }
        ),
        status_code,
    )


@organization_bp.get("/me")
@auth_required
def get_my_organization():
    if not g.current_user.organization_id:
        return jsonify({"error": "Usuário sem organização"}), 400

    org = Organization.query.get_or_404(g.current_user.organization_id)
    return jsonify({"organization": organization_schema.dump(org)})


@organization_bp.patch("/me")
@admin_required
def update_my_organization():
    payload = json_payload()
    allowed = {"nome", "cnpj", "email", "telefone"}
    if not payload or set(payload) - allowed:
        return jsonify({"error": "validation_error", "message": "Informe apenas nome, CNPJ, email e telefone."}), 400
    org = db.session.get(Organization, g.current_user.organization_id)
    if "nome" in payload:
        nome = payload["nome"]
        if not isinstance(nome, str) or not nome.strip() or len(nome.strip()) > 255:
            return jsonify({"error": "validation_error", "message": "Nome da organização inválido."}), 400
        if Organization.query.filter(func.lower(Organization.nome) == nome.strip().lower(), Organization.id != org.id).first():
            return jsonify({"error": "name_in_use", "message": "Nome da organização já está em uso."}), 409
        org.nome = nome.strip()
    if "cnpj" in payload:
        cnpj = normalize_cnpj(payload["cnpj"]) if isinstance(payload["cnpj"], str) else ""
        if cnpj and not is_valid_cnpj(cnpj):
            return jsonify({"error": "validation_error", "message": "CNPJ inválido."}), 400
        if cnpj and Organization.query.filter(Organization.cnpj == cnpj, Organization.id != org.id).first():
            return jsonify({"error": "cnpj_in_use", "message": "CNPJ já está em uso."}), 409
        org.cnpj = cnpj or None
    for field, limit in (("email", 255), ("telefone", 64)):
        if field in payload:
            value = payload[field]
            if value is not None and (not isinstance(value, str) or len(value.strip()) > limit):
                return jsonify({"error": "validation_error", "message": f"{field.capitalize()} inválido."}), 400
            value = value.strip() if isinstance(value, str) else ""
            if field == "email" and value and ("@" not in value or value.startswith("@") or value.endswith("@")):
                return jsonify({"error": "validation_error", "message": "Email inválido."}), 400
            setattr(org, field, value or None)
    db.session.commit()
    return jsonify({"organization": organization_schema.dump(org)})


@organization_bp.get("/me/integrations/portal-unico")
@auth_required
def get_portal_unico_settings():
    try:
        return jsonify(_portal_unico_service().status())
    except PortalUnicoIntegrationError as exc:
        return _portal_error(exc, 503)


@organization_bp.put("/me/integrations/portal-unico")
@admin_required
def configure_portal_unico():
    if (
        current_app.config.get("APP_ENV") == "production"
        and current_app.config.get("PORTAL_UNICO_CREDENTIAL_STORAGE_PROVIDER")
        == "local_encrypted_file"
    ):
        return jsonify({
            "error": "persistent_storage_required",
            "message": "O cadastro do Portal Único exige armazenamento persistente neste ambiente.",
        }), 409
    service = None
    rotation = None
    try:
        payload = portal_unico_schema.load(json_payload())
        service = _portal_unico_service()
        rotation = service.configure(**payload)
        db.session.commit()
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except (PortalUnicoSettingsError, PortalUnicoIntegrationError) as exc:
        db.session.rollback()
        return _portal_error(exc)
    except Exception:
        db.session.rollback()
        if service and rotation:
            service.cleanup_reference(rotation.new_reference)
        raise

    service.cleanup_reference(rotation.previous_reference)
    return jsonify(service.public_data(rotation.connection))


@organization_bp.post("/me/integrations/portal-unico/test")
@admin_required
def test_portal_unico_connection():
    service = _portal_unico_service()
    try:
        result = service.test_connection()
        db.session.commit()
        return jsonify(result)
    except PortalUnicoHealthcheckError as exc:
        db.session.commit()
        return jsonify(
            {
                "error": "portal_unico_healthcheck_failed",
                "message": str(exc),
                **exc.result,
            }
        ), 422
    except PortalUnicoSettingsError as exc:
        db.session.rollback()
        return _portal_error(exc, 409)
    except PortalUnicoIntegrationError as exc:
        db.session.rollback()
        return _portal_error(exc, 503)
