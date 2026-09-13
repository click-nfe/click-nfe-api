from datetime import datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, or_

from app.models.scope import ScopeTemplate
from app.schemas.scope import ScopeTemplateSchema
from app.scope_defaults import merge_scope_draft

from ..auth import auth_required
from ..extensions import db
from ..models import Client, Scope, ScopeAssignment, ScopeVersion, User
from ..schemas import ScopeBulkResponsibleSchema, ScopeListQuerySchema, ScopeSchema, UserSchema
from ..services.scope_processor import ClientScopeConflictError, ScopeDataProcessor

scope_bp = Blueprint("scopes", __name__, url_prefix="/scopes")
scope_schema = ScopeSchema()
scope_list_query_schema = ScopeListQuerySchema()
bulk_responsible_schema = ScopeBulkResponsibleSchema()


def _processor() -> ScopeDataProcessor:
    return ScopeDataProcessor(current_user=g.current_user)


def _client_scope_conflict_response(exc: ClientScopeConflictError):
    db.session.rollback()
    return jsonify(
        {
            "error": "client_scope_already_exists",
            "message": str(exc),
            "client_id": exc.client_id,
            "scope_id": exc.scope_id,
        }
    ), 409


def _load_scope_payload() -> dict:
    payload = request.get_json(force=True)
    return payload if isinstance(payload, dict) else {}


def _load_optional_json_payload() -> dict:
    payload = request.get_json(silent=True) or {}
    return payload if isinstance(payload, dict) else {}


def _admin_forbidden_response():
    if getattr(g.current_user, "role", None) != "admin":
        return jsonify({"error": "forbidden", "message": "Endpoint permitido apenas para admin."}), 403
    return None


def _serialize_responsibles() -> list[dict]:
    query = User.query.filter_by(ativo=True)
    if g.current_user.organization_id:
        query = query.filter_by(organization_id=g.current_user.organization_id)

    users = query.order_by(User.nome.asc()).all()
    return [
        {
            "id": str(user.id),
            "nome": user.nome,
            "email": user.email,
            "role": user.role,
            "setor": user.setor,
        }
        for user in users
    ]


@scope_bp.get("/metadata")
@auth_required
def get_scope_metadata():
    processor = _processor()
    return jsonify({"informacoesFixas": processor.get_admin_settings(), "responsaveis": _serialize_responsibles()})


@scope_bp.post("")
@auth_required
def create_scope():
    template_id = request.args.get("templateId", None)
    client_id = request.args.get("clientId", None)
    processor = _processor()

    if template_id:
        template = processor.template_query_for_current_user().filter(ScopeTemplate.id == template_id).first_or_404()
        draft = template.draft
    else:
        draft = processor.normalize_draft(_load_scope_payload())

    client = None
    if client_id:
        client = processor.get_client_for_current_user(client_id)
        draft = processor.prefill_client_draft(client, draft)

    scope = Scope(
        organization_id=g.current_user.organization_id,
        created_by_id=g.current_user.id,
        client_id=client.id if client else None,
        draft=draft,
        version=1,
    )
    if client:
        try:
            processor.ensure_client_available(scope, client)
        except ClientScopeConflictError as exc:
            return _client_scope_conflict_response(exc)
    processor.apply_draft_to_scope(scope, draft)

    db.session.add(scope)
    db.session.flush()

    try:
        processor.upsert_client_from_draft(scope, draft)
    except ClientScopeConflictError as exc:
        return _client_scope_conflict_response(exc)
    processor.sync_assignments_from_draft(scope, draft)
    processor.sync_services_from_draft(scope, draft)
    processor.sync_prepostos_from_draft(scope, draft)

    db.session.commit()
    return jsonify({"id": str(scope.id)}), 201


@scope_bp.get("")
@auth_required
def list_scopes():
    processor = _processor()
    params = scope_list_query_schema.load(request.args)

    query = processor.scope_query_for_current_user()

    if params.get("status"):
        query = query.filter(Scope.status == params["status"])
    if params.get("client_id"):
        query = query.filter(Scope.client_id == params["client_id"])
    if params.get("responsible_user_id"):
        query = query.filter(Scope.responsible_user_id == params["responsible_user_id"])
    if params.get("created_by_id"):
        query = query.filter(Scope.created_by_id == params["created_by_id"])
    if params.get("assigned_user_id"):
        query = query.join(ScopeAssignment, ScopeAssignment.scope_id == Scope.id).filter(
            ScopeAssignment.user_id == params["assigned_user_id"]
        )
    if params.get("cnpj"):
        query = query.join(Client, Scope.client_id == Client.id).filter(Client.cnpj == params["cnpj"])
    if params.get("q"):
        term = f"%{params['q']}%"
        query = query.outerjoin(Client, Scope.client_id == Client.id).filter(
            or_(Client.razao_social.ilike(term), Client.nome_resumido.ilike(term), Client.cnpj.ilike(term), Scope.status.ilike(term))
        )

    total = query.count()
    scopes = (
        query.order_by(Scope.updated_at.desc().nullslast(), Scope.created_at.desc())
        .limit(params["limit"])
        .offset(params["offset"])
        .all()
    )

    return jsonify(
        {
            "items": [processor.build_scope_summary(scope) for scope in scopes],
            "total": total,
            "limit": params["limit"],
            "offset": params["offset"],
        }
    )

@scope_bp.get("/bulk/assignment-summary")
@auth_required
def get_bulk_assignment_summary():

    processor = _processor()
    group_by = request.args.get("groupBy")

    try:
        return jsonify(processor.get_bulk_assignment_summary(group_by))
    except ValueError as exc:
        return jsonify({"error": "bad_request", "message": str(exc)}), 400


@scope_bp.get("/bulk/assignment-scopes")
@auth_required
def get_bulk_assignment_scopes():

    processor = _processor()
    group_by = request.args.get("groupBy")
    user_id = request.args.get("userId")

    if not user_id:
        return jsonify({"error": "bad_request", "message": "userId é obrigatório."}), 400

    try:
        return jsonify(processor.get_bulk_assignment_scopes(group_by, user_id))
    except ValueError as exc:
        return jsonify({"error": "bad_request", "message": str(exc)}), 400


@scope_bp.post("/bulk/assignment-update")
@auth_required
def bulk_update_assignment():

    processor = _processor()
    payload = _load_optional_json_payload()

    try:
        result = processor.bulk_update_assignment(
            group_by=payload.get("groupBy"),
            from_user_id=payload.get("fromUserId"),
            to_user_id=payload.get("toUserId"),
            scope_ids=payload.get("scopeIds") or [],
        )
    except ValueError as exc:
        return jsonify({"error": "bad_request", "message": str(exc)}), 400

    db.session.commit()
    return jsonify(result)


@scope_bp.get("/user/assigned-count")
@auth_required
def count_user_assigned_scopes():
    processor = _processor()

    query = processor.scope_query_for_current_user()

    query_responsible_user_id = query.filter(Scope.responsible_user_id == g.current_user.id)
    query_created_by_id = query.filter(Scope.created_by_id == g.current_user.id)
    query_assigned_user_id = query.join(ScopeAssignment, ScopeAssignment.scope_id == Scope.id).filter(ScopeAssignment.user_id == g.current_user.id)


    return jsonify([
        {
            "type": "responsible_user_id",
            "count": query_responsible_user_id.count()
        },
        {
            "type": "created_by_id",
            "count": query_created_by_id.count()
        },
        {
            "type": "assigned_user_id",
            "count": query_assigned_user_id.count()
        }
    ])

@scope_bp.get("/templates")
@auth_required
def get_scope_templates():
    processor = _processor()

    query = processor.list_organization_scope_templates()

    templates = (
        query.order_by(ScopeTemplate.updated_at.desc().nullslast(), ScopeTemplate.created_at.desc())
        .all()
    )

    return jsonify(ScopeTemplateSchema(many=True).dump(templates))

@scope_bp.post("/templates")
@auth_required
def create_scope_template():
    payload = request.get_json(force=True)

    processor = _processor()
    draft = processor.normalize_draft(payload.get('draft', {}))

    template = ScopeTemplate(
        name=payload.get("name", "Sem Nome"),
        description=payload.get("description", None),
        organization_id=g.current_user.organization_id,
        draft=draft,
        created_by_id=g.current_user.id,
    )

    db.session.add(template)
    db.session.commit()

    return jsonify(ScopeTemplateSchema().dump(template))

@scope_bp.delete("/templates/<template_id>")
@auth_required
def delete_scope_template(template_id: int):
    processor = _processor()
    template = processor.template_query_for_current_user().filter(ScopeTemplate.id == template_id).first_or_404()
    

    db.session.delete(template)
    db.session.commit()

    return jsonify({'deleted': True})

@scope_bp.get("/templates/<template_id>")
@auth_required
def get_scope_template_by_id(template_id: int):
    processor = _processor()
    template = processor.template_query_for_current_user().filter(ScopeTemplate.id == template_id).first_or_404()

    return jsonify(ScopeTemplateSchema().dump(template))

@scope_bp.put("/templates/<template_id>")
@auth_required
def update_scope_template(template_id: int):
    processor = _processor()
    template = processor.template_query_for_current_user().filter(ScopeTemplate.id == template_id).first_or_404()

    payload = request.get_json(force=True)

    processor = _processor()
    draft = processor.normalize_draft(payload.get('draft', {}))

    template.draft = draft
    template.name = payload.get('name', "Sem Nome")
    template.description = payload.get("description", None),

    db.session.commit()

    return jsonify(ScopeTemplateSchema().dump(template))


@scope_bp.get("/<scope_id>")
@auth_required
def get_scope(scope_id: str):
    processor = _processor()
    scope = processor.get_scope_for_current_user(scope_id)

    return jsonify(
        {
            **scope_schema.dump(scope),
            "created_by": UserSchema(only=["id", "nome", "email", "role", "setor"]).dump(scope.created_by),
        }
    )


@scope_bp.put("/<scope_id>")
@auth_required
def update_scope(scope_id: str):
    processor = _processor()
    scope = processor.get_scope_for_current_user(scope_id)
    normalized_draft = processor.normalize_draft(_load_scope_payload())

    processor.apply_draft_to_scope(scope, normalized_draft)
    try:
        processor.upsert_client_from_draft(scope, normalized_draft)
    except ClientScopeConflictError as exc:
        return _client_scope_conflict_response(exc)
    processor.sync_assignments_from_draft(scope, normalized_draft)
    processor.sync_services_from_draft(scope, normalized_draft)
    processor.sync_prepostos_from_draft(scope, normalized_draft)

    db.session.commit()
    return jsonify(scope_schema.dump(scope))


@scope_bp.post("/<scope_id>/publish")
@auth_required
def publish_scope(scope_id: str):
    processor = _processor()
    scope = processor.get_scope_for_current_user(scope_id)
    now = datetime.now()

    normalized_draft = processor.normalize_draft(scope.draft)
    processor.apply_draft_to_scope(scope, normalized_draft)
    try:
        processor.upsert_client_from_draft(scope, normalized_draft)
    except ClientScopeConflictError as exc:
        return _client_scope_conflict_response(exc)
    processor.sync_assignments_from_draft(scope, normalized_draft)
    processor.sync_services_from_draft(scope, normalized_draft)
    processor.sync_prepostos_from_draft(scope, normalized_draft)

    scope.status = "published"
    scope.last_published_at = now
    scope.published_snapshot = normalized_draft
    scope.version = (scope.version or 0) + 1

    db.session.flush()
    db.session.add(
        ScopeVersion(
            scope_id=scope.id,
            version_number=scope.version,
            draft_snapshot=normalized_draft,
            published_snapshot=normalized_draft,
            created_by_id=g.current_user.id,
        )
    )

    db.session.commit()
    return jsonify({"scope_id": str(scope.id), "published_at": now.isoformat() + "Z", "client_id": str(scope.client_id)})


@scope_bp.post("/<scope_id>/sync")
@auth_required
def sync_scope(scope_id: str):
    """Sincroniza um escopo específico com a arquitetura relacional.

    Body opcional:
        {"dryRun": true}

    Se dryRun=true, apenas retorna o que está faltando.
    Se dryRun=false, cria/atualiza Client, ScopeAssignment, ScopeService e ScopePreposto.
    """
    processor = _processor()
    payload = request.get_json(silent=True) or {}
    dry_run = bool(payload.get("dryRun", True))

    scope = processor.get_scope_for_current_user(scope_id)
    try:
        result = processor.sync_scope(scope, dry_run=dry_run)
    except ClientScopeConflictError as exc:
        return _client_scope_conflict_response(exc)

    if not dry_run and result.changed:
        db.session.commit()

    return jsonify(result.to_dict())


@scope_bp.post("/sync-missing")
@auth_required
def sync_missing_scopes():
    """Sincroniza escopos antigos da organização atual em lote.

    Body opcional:
        {
            "dryRun": true,
            "limit": 100,
            "status": "published"
        }
    """
    processor = _processor()
    payload = request.get_json(silent=True) or {}
    dry_run = bool(payload.get("dryRun", True))
    limit = min(int(payload.get("limit", 100)), 500)

    query = processor.scope_query_for_current_user().order_by(Scope.created_at.asc())
    if payload.get("status"):
        query = query.filter(Scope.status == payload["status"])

    scopes = query.limit(limit).all()
    try:
        results = processor.sync_scopes(scopes, dry_run=dry_run)
    except ClientScopeConflictError as exc:
        return _client_scope_conflict_response(exc)

    if not dry_run:
        db.session.commit()

    return jsonify(
        {
            "dryRun": dry_run,
            "checked": len(results),
            "alreadySynced": sum(1 for item in results if item.already_synced),
            "changed": sum(1 for item in results if item.changed),
            "items": [item.to_dict() for item in results],
        }
    )


@scope_bp.get("/<scope_id>/versions")
@auth_required
def list_scope_versions(scope_id: str):
    processor = _processor()
    scope = processor.get_scope_for_current_user(scope_id)
    rows = (
        ScopeVersion.query.filter_by(scope_id=scope.id)
        .order_by(ScopeVersion.version_number.desc())
        .all()
    )
    return jsonify(
        [
            {
                "id": str(row.id),
                "version_number": row.version_number,
                "created_at": row.created_at.isoformat() + "Z",
                "created_by_id": str(row.created_by_id),
            }
            for row in rows
        ]
    )


@scope_bp.delete("/<scope_id>")
@auth_required
def delete_scope(scope_id: str):
    processor = _processor()
    scope = processor.get_scope_for_current_user(scope_id)
    db.session.delete(scope)
    db.session.commit()
    return "", 204
