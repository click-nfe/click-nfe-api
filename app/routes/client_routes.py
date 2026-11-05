from datetime import datetime
from flask import Blueprint, current_app, g, jsonify, request
from marshmallow import ValidationError
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..auth import ADMIN_ROLE, auth_required
from ..extensions import db
from ..integrations.brasil_api import (
    BrasilApiCnpjClient,
    BrasilApiNotFoundError,
    BrasilApiUnavailableError,
)
from ..models import Client, SimplifiedIssuanceTemplate
from ..services.import_process import ImportNfeService
from ..services.simplified_issuance import normalize_config
from ..schemas.import_process import ImportProcessSchema
from ..schemas import (
    ClientCreateSchema,
    ClientListQuerySchema,
    ClientSchema,
    ClientUpdateSchema,
)
from .route_helpers import json_payload, uuid_or_404, validation_error_response

client_bp = Blueprint("clients", __name__, url_prefix="/clients")
client_schema = ClientSchema()
client_create_schema = ClientCreateSchema()
client_list_query_schema = ClientListQuerySchema()
client_update_schema = ClientUpdateSchema()


def _client_query_for_user():
    return Client.query.filter(
        Client.organization_id == g.current_user.organization_id
    )


@client_bp.get("/lookup/cnpj/<cnpj>")
@auth_required
def lookup_client_by_cnpj(cnpj: str):
    lookup = BrasilApiCnpjClient(
        base_url=current_app.config.get(
            "BRASIL_API_BASE_URL",
            "https://brasilapi.com.br/api",
        ),
        timeout_seconds=current_app.config.get(
            "BRASIL_API_TIMEOUT_SECONDS",
            8,
        ),
    )
    try:
        return jsonify(lookup.lookup(cnpj))
    except ValueError as exc:
        return jsonify(
            {
                "error": "invalid_cnpj",
                "message": str(exc),
            }
        ), 400
    except BrasilApiNotFoundError as exc:
        return jsonify(
            {
                "error": "company_not_found",
                "message": str(exc),
            }
        ), 404
    except BrasilApiUnavailableError as exc:
        return jsonify(
            {
                "error": "company_lookup_unavailable",
                "message": str(exc),
            }
        ), 503


@client_bp.post("")
@auth_required
def create_client():
    organization_id = g.current_user.organization_id
    if not organization_id:
        return jsonify(
            {
                "error": "organization_required",
                "message": "O usuário precisa estar vinculado a uma organização.",
            }
        ), 400

    try:
        payload = client_create_schema.load(json_payload())
    except ValidationError as exc:
        return validation_error_response(exc)

    existing = Client.query.filter(
        Client.organization_id == organization_id,
        Client.cnpj == payload["cnpj"],
    ).first()
    if existing:
        return jsonify(
            {
                "error": "client_already_exists",
                "message": "Já existe um cliente com este CNPJ na organização.",
                "client_id": str(existing.id),
            }
        ), 409

    client = Client(organization_id=organization_id, **payload)
    db.session.add(client)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = Client.query.filter(
            Client.organization_id == organization_id,
            Client.cnpj == payload["cnpj"],
        ).first()
        return jsonify(
            {
                "error": "client_already_exists",
                "message": "Já existe um cliente com este CNPJ na organização.",
                "client_id": str(existing.id) if existing else None,
            }
        ), 409

    return jsonify(client_schema.dump(client)), 201


@client_bp.get("")
@auth_required
def list_clients():
    params = client_list_query_schema.load(request.args)
    query = _client_query_for_user()

    if params.get("cnpj"):
        query = query.filter(Client.cnpj == params["cnpj"])
    if params.get("ativo") is not None:
        query = query.filter(Client.ativo == params["ativo"])
    if params.get("q"):
        term = f"%{params['q']}%"
        query = query.filter(or_(Client.razao_social.ilike(term), Client.nome_resumido.ilike(term), Client.cnpj.ilike(term)))

    total = query.count()
    rows = (
        query.order_by(Client.razao_social.asc())
        .limit(params["limit"])
        .offset(params["offset"])
        .all()
    )

    return jsonify(
        {
            "items": client_schema.dump(rows, many=True),
            "total": total,
            "limit": params["limit"],
            "offset": params["offset"],
        }
    )


@client_bp.get("/<client_id>")
@auth_required
def get_client(client_id: str):
    client = _client_query_for_user().filter(
        Client.id == uuid_or_404(client_id)
    ).first_or_404()
    return jsonify(client_schema.dump(client))


@client_bp.patch("/<client_id>")
@auth_required
def update_client(client_id: str):
    client = _client_query_for_user().filter(
        Client.id == uuid_or_404(client_id)
    ).first_or_404()
    payload = client_update_schema.load(request.get_json(force=True))

    for key, value in payload.items():
        setattr(client, key, value)

    db.session.commit()
    return jsonify(client_schema.dump(client))


@client_bp.get("/<client_id>/simplified-issuance")
@auth_required
def get_simplified_issuance(client_id: str):
    client = _client_query_for_user().filter(Client.id == uuid_or_404(client_id)).first_or_404()
    return jsonify({"config": client.simplified_issuance_config, "updated_at": client.simplified_issuance_updated_at.isoformat() if client.simplified_issuance_updated_at else None})


@client_bp.put("/<client_id>/simplified-issuance")
@auth_required
def put_simplified_issuance(client_id: str):
    client = _client_query_for_user().filter(Client.id == uuid_or_404(client_id)).first_or_404()
    try:
        config = normalize_config(json_payload())
    except ValueError as exc:
        return jsonify({"error": "invalid_simplified_issuance", "message": str(exc)}), 400
    if g.current_user.role != ADMIN_ROLE and any(
        item["tax_configuration"].get("icms_tax_treatment_confirmed") is True
        for item in config["items"]
    ):
        return jsonify({"error": "forbidden", "message": "A confirmação do tratamento fiscal exige um administrador."}), 403
    client.simplified_issuance_config = config
    client.simplified_issuance_updated_at = datetime.utcnow()
    legacy = SimplifiedIssuanceTemplate.query.filter_by(client_id=client.id, organization_id=client.organization_id, name="Configuração existente", active=True).first()
    if legacy:
        legacy.config = config
        legacy.updated_at = client.simplified_issuance_updated_at
    else:
        db.session.add(SimplifiedIssuanceTemplate(organization_id=client.organization_id, client_id=client.id, name="Configuração existente", config=config))
    db.session.commit()
    return jsonify({"config": config, "updated_at": client.simplified_issuance_updated_at.isoformat()})


@client_bp.post("/<client_id>/simplified-issuance/start")
@auth_required
def start_simplified_issuance(client_id: str):
    client = _client_query_for_user().filter(Client.id == uuid_or_404(client_id)).first_or_404()
    data = json_payload()
    if not isinstance(data, dict) or set(data) - {"duimp_number", "template_id", "config"}:
        return jsonify({"error": "invalid_request", "message": "Parâmetros não reconhecidos."}), 400
    template = None
    if data.get("template_id"):
        template = SimplifiedIssuanceTemplate.query.filter_by(id=uuid_or_404(data["template_id"]), client_id=client.id, organization_id=client.organization_id, active=True).first_or_404()
    try:
        config = normalize_config(data["config"]) if data.get("config") is not None else template.config if template else client.simplified_issuance_config
    except ValueError as exc:
        return jsonify({"error": "invalid_simplified_issuance", "message": str(exc)}), 400
    if data.get("config") is not None and g.current_user.role != ADMIN_ROLE:
        confirmed = {(item.get("reference"), item.get("ncm")): item.get("tax_configuration") for item in ((template.config if template else {}).get("items") or []) if item.get("tax_configuration", {}).get("icms_tax_treatment_confirmed") is True}
        if any(item["tax_configuration"].get("icms_tax_treatment_confirmed") is True and item["tax_configuration"] != confirmed.get((item["reference"], item["ncm"])) for item in config["items"]):
            return jsonify({"error": "forbidden", "message": "A confirmação do tratamento fiscal exige um administrador."}), 403
    if not client.ativo or not config:
        return jsonify({"error": "simplified_issuance_not_configured", "message": "Configure a emissão simplificada deste cliente antes de iniciar."}), 400
    service = ImportNfeService(current_user=g.current_user)
    if data.get("duimp_number") is not None and not isinstance(data["duimp_number"], str):
        return jsonify({"error": "invalid_duimp_number", "message": "Número da DUIMP inválido."}), 400
    duimp_number = (data.get("duimp_number") or "").strip()
    if duimp_number and (len(duimp_number) > 30 or not all(char.isalnum() or char == "-" for char in duimp_number)):
        return jsonify({"error": "invalid_duimp_number", "message": "Número da DUIMP inválido."}), 400
    process = service.create_import_process({"importer_id": client.id, "source": "simplified", "duimp_number": duimp_number or None, "simplified_config": config})
    db.session.commit()
    return jsonify(ImportProcessSchema().dump(process)), 201


def _template_response(template):
    return {"id": str(template.id), "name": template.name, "description": template.description or "", "config": template.config,
            "created_at": template.created_at.isoformat() if template.created_at else None,
            "updated_at": template.updated_at.isoformat() if template.updated_at else None}


def _template_query(client_id):
    client = _client_query_for_user().filter(Client.id == uuid_or_404(client_id)).first_or_404()
    return client, SimplifiedIssuanceTemplate.query.filter_by(client_id=client.id, organization_id=client.organization_id, active=True)


@client_bp.get("/<client_id>/simplified-issuance/templates")
@auth_required
def list_simplified_templates(client_id):
    _, query = _template_query(client_id)
    return jsonify({"items": [{"id": str(row.id), "name": row.name, "description": row.description or "", "item_count": len((row.config or {}).get("items") or []),
                               "updated_at": row.updated_at.isoformat() if row.updated_at else None} for row in query.order_by(SimplifiedIssuanceTemplate.created_at.desc()).all()]})


@client_bp.post("/<client_id>/simplified-issuance/templates")
@auth_required
def create_simplified_template(client_id):
    client, _ = _template_query(client_id)
    data = json_payload()
    try:
        name = data.get("name")
        description = data.get("description") or ""
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or not isinstance(description, str) or len(description) > 500:
            raise ValueError("Informe um nome de até 120 caracteres e uma descrição de até 500 caracteres.")
        config = normalize_config(data.get("config"))
    except (ValueError, AttributeError) as exc:
        return jsonify({"error": "invalid_simplified_issuance", "message": str(exc)}), 400
    if g.current_user.role != ADMIN_ROLE and any(item["tax_configuration"].get("icms_tax_treatment_confirmed") is True for item in config["items"]):
        return jsonify({"error": "forbidden", "message": "A confirmação do tratamento fiscal exige um administrador."}), 403
    template = SimplifiedIssuanceTemplate(organization_id=client.organization_id, client_id=client.id, name=name.strip(), description=description.strip(), config=config)
    db.session.add(template)
    db.session.commit()
    return jsonify(_template_response(template)), 201


@client_bp.get("/<client_id>/simplified-issuance/templates/<template_id>")
@auth_required
def get_simplified_template(client_id, template_id):
    _, query = _template_query(client_id)
    return jsonify(_template_response(query.filter_by(id=uuid_or_404(template_id)).first_or_404()))


@client_bp.put("/<client_id>/simplified-issuance/templates/<template_id>")
@auth_required
def update_simplified_template(client_id, template_id):
    _, query = _template_query(client_id)
    template = query.filter_by(id=uuid_or_404(template_id)).first_or_404()
    data = json_payload()
    try:
        name = data.get("name")
        description = data.get("description") or ""
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or not isinstance(description, str) or len(description) > 500:
            raise ValueError("Informe um nome de até 120 caracteres e uma descrição de até 500 caracteres.")
        config = normalize_config(data.get("config"))
    except (ValueError, AttributeError) as exc:
        return jsonify({"error": "invalid_simplified_issuance", "message": str(exc)}), 400
    if g.current_user.role != ADMIN_ROLE and any(item["tax_configuration"].get("icms_tax_treatment_confirmed") is True for item in config["items"]):
        return jsonify({"error": "forbidden", "message": "A confirmação do tratamento fiscal exige um administrador."}), 403
    template.name, template.description, template.config = name.strip(), description.strip(), config
    template.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(_template_response(template))


@client_bp.delete("/<client_id>/simplified-issuance/templates/<template_id>")
@auth_required
def archive_simplified_template(client_id, template_id):
    _, query = _template_query(client_id)
    template = query.filter_by(id=uuid_or_404(template_id)).first_or_404()
    template.active = False
    db.session.commit()
    return "", 204
