from flask import Blueprint, g, jsonify, request

from ..auth import admin_required, auth_required
from ..extensions import db
from ..models import User
from ..schemas import UserSchema
from .route_helpers import uuid_or_404

user_bp = Blueprint("users", __name__, url_prefix="/users")
user_schema = UserSchema()
users_schema = UserSchema(many=True)
ALLOWED_ROLES = {"admin", "comercial", "credenciamento", "operacao"}


def _user_query_for_current_organization():
    return User.query.filter(
        User.organization_id == g.current_user.organization_id
    )


@user_bp.get("")
@admin_required
def list_users():
    query = (
        _user_query_for_current_organization()
        .filter(User.ativo.is_(True))
        .order_by(User.nome.asc())
    )
    users = query.all()

    return jsonify(UserSchema(many=True).dump(users))


@user_bp.get("/responsibles")
@auth_required
def list_responsibles():
    query = (
        _user_query_for_current_organization()
        .filter(User.ativo.is_(True))
        .order_by(User.nome.asc())
    )
    users = query.all()
    return jsonify(
        [
            {
                "id": user.id,
                "nome": user.nome,
                "email": user.email,
                "role": user.role,
                "setor": user.setor,
            }
            for user in users
        ]
    )


@user_bp.post("")
@admin_required
def create_user():
    payload = request.get_json(force=True)
    role = payload.get("role")
    if role not in ALLOWED_ROLES:
        allowed = ", ".join(sorted(ALLOWED_ROLES))
        return jsonify(
            {
                "ok": False,
                "message": f"Os papéis devem ser um dos seguintes: {allowed}",
            }
        ), 400

    if User.query.filter_by(email=payload["email"]).first():
        return jsonify({"ok": False, "message": "Email já está em uso"}), 409

    user = User(
        nome=payload["nome"],
        email=payload["email"],
        role=role,
        setor=payload.get("setor"),
        ativo=payload.get("ativo", True),
        organization_id=g.current_user.organization_id,
    )
    if payload.get("password"):
        user.set_password(payload["password"])

    db.session.add(user)
    db.session.commit()
    return jsonify({"ok": True, "data": user_schema.dump(user)}), 201


@user_bp.put("/user/<user_id>")
@admin_required
def update_user(user_id: str):
    user = _user_query_for_current_organization().filter(
        User.id == uuid_or_404(user_id)
    ).first_or_404()

    payload = request.get_json(force=True)

    user.nome = payload["nome"]
    user.email = payload["email"]
    user.setor = payload.get("setor")
    user.ativo = payload.get("ativo", True)
    if payload.get("password"):
        user.set_password(payload["password"])

    db.session.commit()
    return jsonify({"ok": True, "data": user_schema.dump(user)}), 201


@user_bp.delete("/user/<user_id>")
@admin_required
def delete_user(user_id: str):
    user = _user_query_for_current_organization().filter(
        User.id == uuid_or_404(user_id)
    ).first_or_404()
    user.ativo = False
    db.session.commit()
    return "", 204
