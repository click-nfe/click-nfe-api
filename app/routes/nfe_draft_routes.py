from flask import Blueprint, Response, current_app, g, jsonify
from marshmallow import ValidationError

from ..auth import admin_required, auth_required
from ..extensions import db
from ..models.import_process import NfeDraftItem, NfeXmlVersion
from ..schemas.import_process import (
    GenerateXmlSchema,
    DeleteNfeDraftSchema,
    NfeDraftTaxAdjustmentSchema,
    NfeDraftItemSchema,
    NfeDraftSchema,
    NfeXmlVersionSchema,
    UpdateNfeDraftSchema,
    UpdateNfeDraftItemSchema,
)
from ..schemas.fiscal_certificate import SignNfeXmlSchema
from ..services.fiscal_certificate import (
    FiscalCertificateError,
    certificate_vault_from_config,
)
from ..services.import_process import ImportNfeService
from ..services.nfe_xml_signer import NfeXmlSignatureError, NfeXmlSigner
from ..services.nfe_xsd_validator import (
    NfeXsdConfigurationError,
    NfeXsdValidator,
)
from .route_helpers import (
    bad_request_response,
    json_payload,
    uuid_or_404,
    validation_error_response,
)


nfe_draft_bp = Blueprint("nfe_drafts", __name__, url_prefix="/nfe-drafts")

nfe_draft_schema = NfeDraftSchema()
nfe_draft_item_schema = NfeDraftItemSchema()
nfe_xml_version_schema = NfeXmlVersionSchema()


def _service() -> ImportNfeService:
    return ImportNfeService(
        current_user=g.current_user,
        xsd_validator=NfeXsdValidator(
            schema_path=current_app.config.get("NFE_XSD_PATH")
        ),
        certificate_vault=certificate_vault_from_config(current_app.config),
        xml_signer=(
            current_app.config.get("NFE_XML_SIGNER")
            or NfeXmlSigner()
        ),
    )


def _draft_and_xml_version(draft_id: str, xml_version_id: str):
    draft_uuid = uuid_or_404(draft_id)
    xml_version_uuid = uuid_or_404(xml_version_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )
    xml_version = NfeXmlVersion.query.filter_by(
        id=xml_version_uuid,
        nfe_draft_id=draft.id,
    ).first_or_404()
    return service, draft, xml_version


@nfe_draft_bp.get("/<draft_id>")
@auth_required
def get_nfe_draft(draft_id: str):
    draft_uuid = uuid_or_404(draft_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )
    detail = service.get_nfe_draft_detail(draft)
    return jsonify(
        {
            "draft": nfe_draft_schema.dump(detail["draft"]),
            "items": nfe_draft_item_schema.dump(detail["items"], many=True),
            "xmlVersions": nfe_xml_version_schema.dump(
                detail["xml_versions"], many=True
            ),
            "auditTrail": detail["audit_trail"],
        }
    )


@nfe_draft_bp.patch("/<draft_id>")
@auth_required
def update_nfe_draft(draft_id: str):
    draft_uuid = uuid_or_404(draft_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )

    try:
        payload = UpdateNfeDraftSchema().load(json_payload())
        detail = service.update_draft_metadata(draft, payload)
        db.session.commit()
        return jsonify(
            {
                "draft": nfe_draft_schema.dump(detail["draft"]),
                "items": nfe_draft_item_schema.dump(
                    detail["items"], many=True
                ),
                "validation": detail["validation"],
                "requires_new_xml": detail["requires_new_xml"],
            }
        )
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@nfe_draft_bp.post("/<draft_id>/validate")
@auth_required
def validate_nfe_draft(draft_id: str):
    draft_uuid = uuid_or_404(draft_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )

    try:
        validation = service.validate_draft(draft)
        db.session.commit()
        return jsonify(validation.to_dict())
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@nfe_draft_bp.patch("/<draft_id>/items/<item_id>")
@auth_required
def update_nfe_draft_item(draft_id: str, item_id: str):
    draft_uuid = uuid_or_404(draft_id)
    item_uuid = uuid_or_404(item_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )
    item = NfeDraftItem.query.filter_by(
        id=item_uuid, nfe_draft_id=draft.id
    ).first_or_404()

    try:
        payload = UpdateNfeDraftItemSchema().load(json_payload())
        item = service.update_draft_item(draft, item, payload)
        db.session.commit()
        return jsonify(nfe_draft_item_schema.dump(item))
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@nfe_draft_bp.patch("/<draft_id>/items/<item_id>/tax-adjustment")
@auth_required
def adjust_nfe_draft_item_tax(draft_id: str, item_id: str):
    draft_uuid = uuid_or_404(draft_id)
    item_uuid = uuid_or_404(item_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )
    item = NfeDraftItem.query.filter_by(
        id=item_uuid, nfe_draft_id=draft.id
    ).first_or_404()

    try:
        payload = NfeDraftTaxAdjustmentSchema().load(json_payload())
        detail = service.adjust_draft_item_tax(draft, item, payload)
        db.session.commit()
        return jsonify(
            {
                "draft": nfe_draft_schema.dump(detail["draft"]),
                "item": nfe_draft_item_schema.dump(detail["item"]),
                "validation": detail["validation"],
                "audit": detail["audit"],
                "requires_new_xml": detail["requires_new_xml"],
            }
        )
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@nfe_draft_bp.delete("/<draft_id>")
@nfe_draft_bp.post("/<draft_id>/remove")
@auth_required
def delete_nfe_draft(draft_id: str):
    draft_uuid = uuid_or_404(draft_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )
    try:
        payload = DeleteNfeDraftSchema().load(json_payload())
        result = service.soft_delete_draft(draft, reason=payload["reason"])
        db.session.commit()
        return jsonify(result)
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@nfe_draft_bp.post("/<draft_id>/generate-access-key")
@auth_required
def generate_nfe_draft_access_key(draft_id: str):
    draft_uuid = uuid_or_404(draft_id)

    try:
        result = _service().generate_access_key_for_draft(draft_uuid)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)

    return jsonify(
        {
            "draft_id": str(result["draft"].id),
            "number": result["draft"].number,
            "access_key": result["access_key"]["access_key"],
            "cUF": result["access_key"]["cUF"],
            "AAMM": result["access_key"]["AAMM"],
            "CNPJ": result["access_key"]["CNPJ"],
            "model": result["access_key"]["model"],
            "series": result["access_key"]["series"],
            "tpEmis": result["access_key"]["tpEmis"],
            "cNF": result["access_key"]["cNF"],
            "cDV": result["access_key"]["cDV"],
            "validation": result["validation"],
        }
    )


@nfe_draft_bp.post("/<draft_id>/generate-xml")
@auth_required
def generate_nfe_unsigned_xml(draft_id: str):
    draft_uuid = uuid_or_404(draft_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )

    try:
        GenerateXmlSchema().load(json_payload())
        row = service.generate_unsigned_xml(draft)
        db.session.commit()
        return jsonify(nfe_xml_version_schema.dump(row)), 201
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@nfe_draft_bp.get("/<draft_id>/xml-versions")
@auth_required
def list_nfe_xml_versions(draft_id: str):
    draft_uuid = uuid_or_404(draft_id)
    service = _service()
    draft = (
        service.nfe_draft_query_for_current_user()
        .filter_by(id=draft_uuid)
        .first_or_404()
    )
    rows = (
        NfeXmlVersion.query.filter(NfeXmlVersion.nfe_draft_id == draft.id)
        .order_by(NfeXmlVersion.version_number.desc())
        .all()
    )
    return jsonify(nfe_xml_version_schema.dump(rows, many=True))


@nfe_draft_bp.get(
    "/<draft_id>/xml-versions/<xml_version_id>/download"
)
@auth_required
def download_nfe_xml_version(draft_id: str, xml_version_id: str):
    _, _, xml_version = _draft_and_xml_version(draft_id, xml_version_id)
    xml_type = getattr(xml_version.xml_type, "value", xml_version.xml_type)
    xml_type = str(xml_type).lower()
    identifier = xml_version.access_key or str(xml_version.id)
    filename = (
        f"NFe-{identifier}-{xml_type}-v{xml_version.version_number}.xml"
    )
    return Response(
        xml_version.xml_content,
        status=200,
        content_type="application/xml; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-NFe-Xml-Version": str(xml_version.version_number),
        },
    )


@nfe_draft_bp.post(
    "/<draft_id>/xml-versions/<xml_version_id>/validate-xsd"
)
@auth_required
def validate_nfe_xml_version_xsd(draft_id: str, xml_version_id: str):
    service, draft, xml_version = _draft_and_xml_version(
        draft_id,
        xml_version_id,
    )
    try:
        result = service.validate_xml_version(draft, xml_version)
        db.session.commit()
    except NfeXsdConfigurationError as exc:
        db.session.rollback()
        return (
            jsonify(
                {
                    "error": "xsd_schema_unavailable",
                    "message": str(exc),
                }
            ),
            503,
        )
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)

    return jsonify(
        {
            "xml_version_id": str(xml_version.id),
            "nfe_draft_id": str(draft.id),
            "version_number": xml_version.version_number,
            "xml_type": getattr(
                xml_version.xml_type,
                "value",
                xml_version.xml_type,
            ),
            "xsd_valid": xml_version.xsd_valid,
            "xsd_errors": xml_version.xsd_errors,
            **result.to_dict(),
        }
    )


@nfe_draft_bp.post(
    "/<draft_id>/xml-versions/<xml_version_id>/sign"
)
@admin_required
def sign_nfe_xml_version(draft_id: str, xml_version_id: str):
    service, draft, xml_version = _draft_and_xml_version(
        draft_id,
        xml_version_id,
    )
    try:
        payload = SignNfeXmlSchema().load(json_payload())
        result = service.sign_xml_version(
            draft,
            xml_version,
            certificate_id=payload.get("certificate_id"),
        )
        db.session.commit()
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except NfeXsdConfigurationError as exc:
        db.session.commit()
        return (
            jsonify(
                {
                    "error": "xsd_schema_unavailable",
                    "message": str(exc),
                }
            ),
            503,
        )
    except (
        FiscalCertificateError,
        NfeXmlSignatureError,
    ) as exc:
        db.session.commit()
        return (
            jsonify(
                {
                    "error": "nfe_signature_error",
                    "message": str(exc),
                }
            ),
            422,
        )
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)

    signed_version = result["xml_version"]
    issuance = result["issuance"]
    certificate = result["certificate"]
    status_code = 200 if result["replayed"] else 201
    return (
        jsonify(
            {
                "xml_version": nfe_xml_version_schema.dump(signed_version),
                "issuance": {
                    "id": str(issuance.id),
                    "status": issuance.status,
                    "certificate_id": str(certificate.id),
                    "access_key": issuance.access_key,
                },
                "certificate": {
                    "id": str(certificate.id),
                    "issuer_cnpj": certificate.issuer_cnpj,
                    "fingerprint_sha256": (
                        certificate.certificate_fingerprint_sha256
                    ),
                    "valid_until": (
                        certificate.valid_until.isoformat()
                        if certificate.valid_until
                        else None
                    ),
                },
                "replayed": result["replayed"],
            }
        ),
        status_code,
    )
