from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Blueprint, Response, g, jsonify, request
from marshmallow import ValidationError

from ..auth import auth_required
from ..extensions import db
from ..models.import_process import (
    DuimpSnapshot,
    ExternalAuthType,
    ExternalConnectionStatus,
    ExternalProvider,
    FiscalEnvironment,
    HttpMethod,
    ImportProcessSource,
    ImportProcessStatus,
    ImportPurpose,
    NfeDraft,
    NfeDraftItem,
    NfeDraftStatus,
    NfeModel,
    NfeOperationType,
    NfePurpose,
    NfeXmlType,
    NfeXmlVersion,
)
from ..schemas.import_process import (
    CreateImportProcessSchema,
    CreateNfeDocumentPlanSchema,
    FetchDuimpSchema,
    GenerateNfeChildDraftsSchema,
    CreateManualDuimpSnapshotSchema,
    CreateNfeDraftFromDuimpSchema,
    DuimpSnapshotSchema,
    ImportProcessListQuerySchema,
    ImportProcessSchema,
    NfeDraftItemSchema,
    NfeDraftSchema,
    NfeWorkflowStateQuerySchema,
    NfeXmlVersionSchema,
    UpdateImportProcessSchema,
)
from ..services.import_process import ImportNfeService
from ..services.nfe_xsd_validator import NfeXsdConfigurationError
from .route_helpers import (
    bad_request_response,
    json_payload,
    uuid_or_404,
    validation_error_response,
)


import_process_bp = Blueprint(
    "import_processes", __name__, url_prefix="/import-processes"
)

import_process_schema = ImportProcessSchema()
duimp_snapshot_schema = DuimpSnapshotSchema()
nfe_draft_schema = NfeDraftSchema()
nfe_draft_item_schema = NfeDraftItemSchema()
nfe_xml_version_schema = NfeXmlVersionSchema()


def _service() -> ImportNfeService:
    return ImportNfeService(current_user=g.current_user)


@import_process_bp.get("/metadata")
@auth_required
def get_import_process_metadata():
    return jsonify(
        {
            "importProcessStatuses": ImportProcessStatus.values(),
            "importProcessSources": ImportProcessSource.values(),
            "externalProviders": ExternalProvider.values(),
            "fiscalEnvironments": FiscalEnvironment.values(),
            "externalAuthTypes": ExternalAuthType.values(),
            "externalConnectionStatuses": ExternalConnectionStatus.values(),
            "httpMethods": HttpMethod.values(),
            "nfeModels": NfeModel.values(),
            "nfePurposes": NfePurpose.values(),
            "nfeOperationTypes": NfeOperationType.values(),
            "nfeDraftStatuses": NfeDraftStatus.values(),
            "nfeXmlTypes": NfeXmlType.values(),
            "importPurposes": ImportPurpose.values(),
        }
    )


@import_process_bp.post("")
@auth_required
def create_import_process():
    try:
        payload = CreateImportProcessSchema().load(json_payload())
        process = _service().create_import_process(payload)
        db.session.commit()
        return jsonify(import_process_schema.dump(process)), 201
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@import_process_bp.get("")
@auth_required
def list_import_processes():
    try:
        params = ImportProcessListQuerySchema().load(request.args)
        return jsonify(_service().list_import_processes(params))
    except ValidationError as exc:
        return validation_error_response(exc)


@import_process_bp.get("/dashboard-summary")
@auth_required
def get_import_process_dashboard_summary():
    return jsonify(_service().get_import_process_dashboard_summary())


@import_process_bp.get("/client-groups")
@auth_required
def list_import_process_client_groups():
    try:
        params = ImportProcessListQuerySchema().load(request.args)
        return jsonify(_service().list_import_process_client_groups(params))
    except ValidationError as exc:
        return validation_error_response(exc)


@import_process_bp.get("/<process_id>")
@auth_required
def get_import_process(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )
    return jsonify(import_process_schema.dump(process))


@import_process_bp.get("/<process_id>/nfe-workflow-state")
@auth_required
def get_nfe_workflow_state(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )

    try:
        params = NfeWorkflowStateQuerySchema().load(request.args)
        state = service.get_nfe_workflow_state(process, params)
        detail = state.get("latest_draft")
        if detail:
            state["latest_draft"] = {
                "draft": nfe_draft_schema.dump(detail["draft"]),
                "items": nfe_draft_item_schema.dump(detail["items"], many=True),
                "xmlVersions": nfe_xml_version_schema.dump(
                    detail["xml_versions"], many=True
                ),
            }
        return jsonify(state)
    except ValidationError as exc:
        return validation_error_response(exc)
    except ValueError as exc:
        return bad_request_response(exc)


@import_process_bp.put("/<process_id>")
@auth_required
def update_import_process(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )

    try:
        payload = UpdateImportProcessSchema().load(json_payload())
        process = service.update_import_process(process, payload)
        db.session.commit()
        return jsonify(import_process_schema.dump(process))
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@import_process_bp.delete("/<process_id>")
@auth_required
def delete_import_process(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )
    db.session.delete(process)
    db.session.commit()
    return "", 204


@import_process_bp.post("/<process_id>/duimp-snapshots")
@auth_required
def create_manual_duimp_snapshot(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )

    try:
        payload = CreateManualDuimpSnapshotSchema().load(json_payload())
        snapshot = service.create_manual_duimp_snapshot(process, payload)
        db.session.commit()
        return jsonify(duimp_snapshot_schema.dump(snapshot)), 201
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@import_process_bp.get("/<process_id>/duimp-snapshots")
@auth_required
def list_duimp_snapshots(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )

    rows = (
        service.snapshot_query_for_current_user()
        .filter(DuimpSnapshot.import_process_id == process_uuid)
        .order_by(DuimpSnapshot.created_at.desc())
        .all()
    )
    return jsonify(duimp_snapshot_schema.dump(rows, many=True))


@import_process_bp.get("/<process_id>/nfe-drafts")
@auth_required
def list_process_nfe_drafts(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )
    rows = (
        service.nfe_draft_query_for_current_user(include_removed=True)
        .filter(NfeDraft.import_process_id == process.id)
        .order_by(
            NfeDraft.created_at.desc(),
            NfeDraft.updated_at.desc().nullslast(),
        )
        .all()
    )

    items = []
    for draft in rows:
        xml_versions = (
            NfeXmlVersion.query
            .filter(NfeXmlVersion.nfe_draft_id == draft.id)
            .order_by(
                NfeXmlVersion.version_number.desc(),
                NfeXmlVersion.generated_at.desc(),
            )
            .all()
        )
        items.append(
            {
                "id": str(draft.id),
                "status": getattr(draft.status, "value", draft.status),
                "environment": getattr(
                    draft.environment, "value", draft.environment
                ),
                "series": draft.series,
                "number": draft.number,
                "access_key": draft.access_key,
                "deleted_at": (
                    draft.deleted_at.isoformat() + "Z"
                    if draft.deleted_at
                    else None
                ),
                "deletion_reason": draft.deletion_reason,
                "deletion_mode": draft.deletion_mode,
                "requires_inutilization_review": (
                    draft.deletion_mode == "archived"
                ),
                "duimp_snapshot_id": (
                    str(draft.duimp_snapshot_id)
                    if draft.duimp_snapshot_id
                    else None
                ),
                "planned_document_id": (
                    str(draft.planned_document_id)
                    if draft.planned_document_id
                    else None
                ),
                "exporter_code": (
                    draft.planned_document.exporter_code
                    if draft.planned_document
                    else None
                ),
                "foreign_supplier": (
                    draft.planned_document.foreign_supplier
                    if draft.planned_document
                    else None
                ),
                "items_count": (
                    NfeDraftItem.query
                    .filter(NfeDraftItem.nfe_draft_id == draft.id)
                    .count()
                ),
                "validation_errors": draft.validation_errors or [],
                "validation_warnings": draft.validation_warnings or [],
                "created_at": (
                    draft.created_at.isoformat() + "Z"
                    if draft.created_at
                    else None
                ),
                "updated_at": (
                    draft.updated_at.isoformat() + "Z"
                    if draft.updated_at
                    else None
                ),
                "xml_versions": [
                    {
                        "id": str(version.id),
                        "version_number": version.version_number,
                        "xml_type": getattr(
                            version.xml_type, "value", version.xml_type
                        ),
                        "xsd_valid": version.xsd_valid,
                        "xsd_errors": version.xsd_errors or [],
                        "access_key": version.access_key,
                        "protocol_number": version.protocol_number,
                        "generated_at": (
                            version.generated_at.isoformat() + "Z"
                            if version.generated_at
                            else None
                        ),
                    }
                    for version in xml_versions
                ],
            }
        )

    return jsonify({"items": items})


@import_process_bp.get("/<process_id>/nfe-document-plan")
@auth_required
def get_process_nfe_document_plan(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )
    snapshot_id = request.args.get("duimp_snapshot_id")
    try:
        return jsonify(service.get_document_plan_state(process, snapshot_id))
    except ValueError as exc:
        return bad_request_response(exc)


@import_process_bp.post("/<process_id>/nfe-document-plan")
@auth_required
def create_process_nfe_document_plan(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )
    try:
        payload = CreateNfeDocumentPlanSchema().load(json_payload())
        plan = service.create_document_plan(process, payload)
        db.session.commit()
        return jsonify(plan), 201
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@import_process_bp.post("/<process_id>/nfe-document-plan/generate-drafts")
@auth_required
def generate_process_nfe_child_drafts(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )
    try:
        payload = GenerateNfeChildDraftsSchema().load(json_payload())
        result = service.generate_child_drafts(process, payload)
        db.session.commit()
        return jsonify(result), 201 if result["created_draft_ids"] else 200
    except ValidationError as exc:
        db.session.rollback()
        return validation_error_response(exc)
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@import_process_bp.post("/<process_id>/nfe-document-plan/generate-xmls")
@auth_required
def generate_process_nfe_child_xmls(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )
    try:
        result = service.generate_and_validate_child_xmls(
            process,
            (json_payload() or {}).get("duimp_snapshot_id"),
        )
        db.session.commit()
        return jsonify(result)
    except NfeXsdConfigurationError as exc:
        db.session.rollback()
        return jsonify({"error": "xsd_schema_unavailable", "message": str(exc)}), 503
    except ValueError as exc:
        db.session.rollback()
        return bad_request_response(exc)


@import_process_bp.get("/<process_id>/nfe-document-plan/xmls/download")
@auth_required
def download_process_nfe_child_xmls(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )
    state = service.get_document_plan_state(
        process,
        request.args.get("duimp_snapshot_id"),
    )
    plan = state.get("plan")
    if not plan:
        return bad_request_response(ValueError("Plano de notas ativo não encontrado."))

    xml_files = []
    for document in plan["documents"]:
        draft = document.get("draft") or {}
        xml = draft.get("latest_xml") or {}
        if not xml.get("id"):
            continue
        row = NfeXmlVersion.query.filter(
            NfeXmlVersion.id == uuid_or_404(xml["id"]),
            NfeXmlVersion.nfe_draft_id == uuid_or_404(draft["id"]),
        ).first()
        if row:
            identifier = draft.get("access_key") or draft["id"]
            xml_files.append(
                (f"NFe-filha-{document['ordinal']}-{identifier}.xml", row.xml_content)
            )
    if not xml_files:
        return bad_request_response(
            ValueError("Nenhum XML de NF-e filha está disponível para download.")
        )

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for filename, content in xml_files:
            archive.writestr(filename, content)
    filename = f"NFe-filhas-{process.duimp_number or process.id}.zip"
    return Response(
        buffer.getvalue(),
        content_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@import_process_bp.post("/<process_id>/duimp/fetch")
@auth_required
def fetch_process_duimp(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )

    try:
        payload = FetchDuimpSchema().load(json_payload())
        result = service.fetch_duimp_for_process(process, payload)
        db.session.commit()
        return (
            jsonify(
                {
                    "snapshot": duimp_snapshot_schema.dump(result["snapshot"]),
                    "normalized": result["normalized"],
                }
            ),
            201,
        )
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


@import_process_bp.post("/<process_id>/nfe-draft/from-duimp")
@auth_required
def create_nfe_draft_from_duimp(process_id: str):
    process_uuid = uuid_or_404(process_id)
    service = _service()
    process = (
        service.import_process_query_for_current_user()
        .filter_by(id=process_uuid)
        .first_or_404()
    )

    try:
        payload = CreateNfeDraftFromDuimpSchema().load(json_payload())
        result = service.create_nfe_draft_from_duimp(process, payload)
        db.session.commit()
        return (
            jsonify(
                {
                    "draft": nfe_draft_schema.dump(result["draft"]),
                    "snapshot": duimp_snapshot_schema.dump(result["snapshot"]),
                    "validation": result["validation"],
                    "tax_rule": result.get("tax_rule"),
                }
            ),
            201,
        )
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
