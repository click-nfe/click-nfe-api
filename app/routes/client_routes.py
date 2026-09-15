from flask import Blueprint, g, jsonify, request
from marshmallow import ValidationError
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..auth import auth_required
from ..extensions import db
from ..models import Client
from ..schemas import (
    ClientCreateSchema,
    ClientListQuerySchema,
    ClientSchema,
    ClientUpdateSchema,
)
from .route_helpers import json_payload, validation_error_response

client_bp = Blueprint("clients", __name__, url_prefix="/clients")
client_schema = ClientSchema()
client_create_schema = ClientCreateSchema()
client_list_query_schema = ClientListQuerySchema()
client_update_schema = ClientUpdateSchema()


def _client_query_for_user():
    q = Client.query
    if g.current_user.organization_id:
        q = q.filter(Client.organization_id == g.current_user.organization_id)
    return q


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
    client = _client_query_for_user().filter(Client.id == client_id).first_or_404()
    return jsonify(client_schema.dump(client))


@client_bp.patch("/<client_id>")
@auth_required
def update_client(client_id: str):
    client = _client_query_for_user().filter(Client.id == client_id).first_or_404()
    payload = client_update_schema.load(request.get_json(force=True))

    for key, value in payload.items():
        setattr(client, key, value)

    db.session.commit()
    return jsonify(client_schema.dump(client))
