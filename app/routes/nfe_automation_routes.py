from flask import Blueprint, g, jsonify, request
from marshmallow import ValidationError

from ..auth import admin_required, auth_required
from ..extensions import db
from ..schemas.nfe_automation import (
    BulkNfeItemClassificationSchema,
    ClientImportTaxRuleSchema,
    NfeContextQuerySchema,
    NfeItemClassificationQuerySchema,
    ResolveNfeContextSchema,
    SimulateClientImportTaxRuleSchema,
    UpdateClientImportTaxRuleSchema,
)
from ..services.import_process import (
    ImportNfeService,
    ImportTaxRuleConflictError,
)
from .route_helpers import (
    bad_request_response,
    json_payload,
    uuid_or_404,
    validation_error_response,
)


client_import_tax_rule_bp = Blueprint(
    "client_import_tax_rules",
    __name__,
    url_prefix="/clients",
)
nfe_context_bp = Blueprint(
    "nfe_context",
    __name__,
    url_prefix="/import-processes",
)

tax_rule_schema = ClientImportTaxRuleSchema()


def _service() -> ImportNfeService:
    return ImportNfeService(current_user=g.current_user)


@client_import_tax_rule_bp.get("/<client_id>/import-tax-rules")
@auth_required
def list_import_tax_rules(client_id: str):
    client_uuid = uuid_or_404(client_id)
    try:
        rows = _service().list_import_tax_rules(client_uuid)
        return jsonify(tax_rule_schema.dump(rows, many=True))
    except ValueError as exc:
        return bad_request_response(exc)


@client_import_tax_rule_bp.get("/<client_id>/import-tax-rules/diagnostics")
@auth_required
def import_tax_rule_diagnostics(client_id: str):
    client_uuid = uuid_or_404(client_id)
    try:
        result = _service().import_tax_rule_diagnostics(client_uuid)
        conflicts_by_rule = result["conflicts_by_rule"]
        items = tax_rule_schema.dump(result["rules"], many=True)
        for item in items:
            item["conflicts"] = conflicts_by_rule.get(str(item["id"]), [])
            item["has_conflicts"] = bool(item["conflicts"])
        return jsonify(
            {
                "items": items,
                "conflicts": result["conflicts"],
                "summary": result["summary"],
            }
        )
    except ValueError as exc:
        return bad_request_response(exc)


@client_import_tax_rule_bp.post("/<client_id>/import-tax-rules/simulate")
@auth_required
def simulate_import_tax_rule(client_id: str):
    client_uuid = uuid_or_404(client_id)
    try:
        payload = SimulateClientImportTaxRuleSchema().load(json_payload())
        return jsonify(
            _service().simulate_import_tax_rule(client_uuid, payload)
        )
    except ValidationError as exc:
        return validation_error_response(exc)
    except ImportTaxRuleConflictError as exc:
        return jsonify(
            {
                "error": "tax_rule_conflict",
                "message": str(exc),
                "conflicts": exc.conflicts,
            }
        ), 409
    except ValueError as exc:
        return bad_request_response(exc)


@client_import_tax_rule_bp.post("/<client_id>/import-tax-rules")
@admin_required
def create_import_tax_rule(client_id: str):
    client_uuid = uuid_or_404(client_id)
    try:
        payload = tax_rule_schema.load(json_payload())
        rule = _service().create_import_tax_rule(client_uuid, payload)
        db.session.commit()
        return jsonify(tax_rule_schema.dump(rule)), 201
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ImportTaxRuleConflictError as exc:
        db.session.rollback()
        return jsonify(
            {
                "error": "tax_rule_conflict",
                "message": str(exc),
                "conflicts": exc.conflicts,
            }
        ), 409
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@client_import_tax_rule_bp.put("/<client_id>/import-tax-rules/<rule_id>")
@admin_required
def update_import_tax_rule(client_id: str, rule_id: str):
    client_uuid = uuid_or_404(client_id)
    rule_uuid = uuid_or_404(rule_id)
    service = _service()
    try:
        payload = UpdateClientImportTaxRuleSchema().load(
            json_payload(),
            partial=True,
        )
        rule = service.get_import_tax_rule(client_uuid, rule_uuid)
        rule = service.update_import_tax_rule(rule, payload)
        db.session.commit()
        return jsonify(tax_rule_schema.dump(rule))
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ImportTaxRuleConflictError as exc:
        db.session.rollback()
        return jsonify(
            {
                "error": "tax_rule_conflict",
                "message": str(exc),
                "conflicts": exc.conflicts,
            }
        ), 409
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@client_import_tax_rule_bp.delete("/<client_id>/import-tax-rules/<rule_id>")
@admin_required
def deactivate_import_tax_rule(client_id: str, rule_id: str):
    client_uuid = uuid_or_404(client_id)
    rule_uuid = uuid_or_404(rule_id)
    service = _service()
    try:
        rule = service.get_import_tax_rule(client_uuid, rule_uuid)
        service.deactivate_import_tax_rule(rule)
        db.session.commit()
        return "", 204
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


def _process(service: ImportNfeService, process_id: str):
    process_uuid = uuid_or_404(process_id)
    return (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )


@nfe_context_bp.get("/<process_id>/item-classifications")
@auth_required
def get_item_classifications(process_id: str):
    service = _service()
    process = _process(service, process_id)
    try:
        payload = NfeItemClassificationQuerySchema().load(request.args)
        return jsonify(
            service.get_item_classification_state(
                process,
                payload.get("duimp_snapshot_id"),
            )
        )
    except ValidationError as exc:
        return validation_error_response(exc)
    except ValueError as exc:
        return bad_request_response(exc)


@nfe_context_bp.put("/<process_id>/item-classifications")
@auth_required
def save_item_classifications(process_id: str):
    service = _service()
    process = _process(service, process_id)
    try:
        payload = BulkNfeItemClassificationSchema().load(json_payload())
        result = service.save_item_classifications(process, payload)
        db.session.commit()
        return jsonify(result)
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@nfe_context_bp.get("/<process_id>/nfe-context")
@auth_required
def get_nfe_context(process_id: str):
    service = _service()
    process = _process(service, process_id)
    try:
        payload = NfeContextQuerySchema().load(request.args)
        result = service.get_nfe_context(process, payload, persist=False)
        return jsonify(result)
    except ValidationError as exc:
        return validation_error_response(exc)
    except ValueError as exc:
        return bad_request_response(exc)


@nfe_context_bp.post("/<process_id>/nfe-context/resolve")
@auth_required
def resolve_nfe_context(process_id: str):
    service = _service()
    process = _process(service, process_id)
    try:
        payload = ResolveNfeContextSchema().load(json_payload())
        result = service.get_nfe_context(process, payload, persist=True)
        db.session.commit()
        return jsonify(result)
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)
    except Exception as exc:
        db.session.rollback()
        return jsonify(
            {"error": "external_integration_error", "message": str(exc)}
        ), 502
