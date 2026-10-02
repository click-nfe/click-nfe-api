from flask import Blueprint, g, jsonify

from ..auth import ACCESS_TAGS, admin_required, auth_required
from ..extensions import db
from ..models import User
from ..schemas import UserSchema
from .route_helpers import json_payload, uuid_or_404

user_bp = Blueprint("users", __name__, url_prefix="/users")
user_schema = UserSchema()
ALLOWED_ROLES = {"admin", "comercial", "credenciamento", "operacao"}


def _user_query_for_current_organization():
    return User.query.filter(User.organization_id == g.current_user.organization_id)


def _validate_user(payload, *, creating=False, existing=None):
    nome = payload.get("nome", existing.nome if existing else None)
    email = payload.get("email", existing.email if existing else None)
    role = payload.get("role", existing.role if existing else None)
    tags = payload.get("access_tags", existing.access_tags if existing else list(ACCESS_TAGS) if role == "admin" else None)
    password = payload.get("password")
    if not isinstance(nome, str) or not nome.strip() or len(nome.strip()) > 255:
        return None, "Informe o nome (até 255 caracteres)."
    if not isinstance(email, str) or "@" not in email or len(email.strip()) > 255:
        return None, "Informe um email válido."
    if role not in ALLOWED_ROLES:
        return None, "Papel de usuário inválido."
    if not isinstance(tags, list) or any(not isinstance(tag, str) or tag not in ACCESS_TAGS for tag in tags):
        return None, "Selecione apenas tags de acesso disponíveis."
    if role != "admin" and not tags:
        return None, "Selecione pelo menos uma tag de acesso."
    if creating and (not isinstance(password, str) or len(password) < 8):
        return None, "A senha inicial deve conter ao menos 8 caracteres."
    if password is not None and (not isinstance(password, str) or (password and len(password) < 8)):
        return None, "A nova senha deve conter ao menos 8 caracteres."
    setor = payload.get("setor", existing.setor if existing else None)
    if setor is not None and (not isinstance(setor, str) or len(setor) > 255):
        return None, "Setor inválido."
    ativo = payload.get("ativo", existing.ativo if existing else True)
    if not isinstance(ativo, bool):
        return None, "Estado do usuário inválido."
    return dict(nome=nome.strip(), email=email.strip().lower(), role=role,
                access_tags=list(ACCESS_TAGS) if role == "admin" else list(dict.fromkeys(tags)),
                setor=setor.strip() or None if isinstance(setor, str) else None, ativo=ativo), None


def _email_in_use(email, excluding=None):
    query = User.query.filter(db.func.lower(User.email) == email)
    if excluding:
        query = query.filter(User.id != excluding)
    return query.first() is not None


@user_bp.get("")
@admin_required
def list_users():
    users = _user_query_for_current_organization().order_by(User.nome.asc()).all()
    return jsonify(UserSchema(many=True).dump(users))


@user_bp.get("/responsibles")
@auth_required
def list_responsibles():
    users = _user_query_for_current_organization().filter(User.ativo.is_(True)).order_by(User.nome.asc()).all()
    return jsonify([{"id": user.id, "nome": user.nome, "email": user.email,
                     "role": user.role, "setor": user.setor} for user in users])


@user_bp.post("")
@admin_required
def create_user():
    values, error = _validate_user(json_payload(), creating=True)
    if error:
        return jsonify({"error": "validation_error", "message": error}), 400
    if _email_in_use(values["email"]):
        return jsonify({"error": "email_in_use", "message": "Email já está em uso."}), 409
    user = User(**values, organization_id=g.current_user.organization_id)
    user.set_password(json_payload()["password"])
    db.session.add(user)
    db.session.commit()
    return jsonify({"ok": True, "data": user_schema.dump(user)}), 201


@user_bp.put("/user/<user_id>")
@admin_required
def update_user(user_id: str):
    user = _user_query_for_current_organization().filter(User.id == uuid_or_404(user_id)).first_or_404()
    payload = json_payload()
    values, error = _validate_user(payload, existing=user)
    if error:
        return jsonify({"error": "validation_error", "message": error}), 400
    if user.id == g.current_user.id and (values["role"] != "admin" or not values["ativo"]):
        return jsonify({"error": "self_lockout", "message": "Não é possível remover seu próprio acesso administrativo."}), 409
    if _email_in_use(values["email"], user.id):
        return jsonify({"error": "email_in_use", "message": "Email já está em uso."}), 409
    if user.role == "admin" and user.ativo and (values["role"] != "admin" or not values["ativo"]):
        if _user_query_for_current_organization().filter(User.role == "admin", User.ativo.is_(True)).count() <= 1:
            return jsonify({"error": "last_admin", "message": "A organização precisa manter um administrador ativo."}), 409
    for field, value in values.items():
        setattr(user, field, value)
    if payload.get("password"):
        user.set_password(payload["password"])
    db.session.commit()
    return jsonify({"ok": True, "data": user_schema.dump(user)})


@user_bp.delete("/user/<user_id>")
@admin_required
def delete_user(user_id: str):
    user = _user_query_for_current_organization().filter(User.id == uuid_or_404(user_id)).first_or_404()
    if user.id == g.current_user.id:
        return jsonify({"error": "self_lockout", "message": "Não é possível desativar seu próprio usuário."}), 409
    if user.role == "admin" and user.ativo and _user_query_for_current_organization().filter(User.role == "admin", User.ativo.is_(True)).count() <= 1:
        return jsonify({"error": "last_admin", "message": "A organização precisa manter um administrador ativo."}), 409
    user.ativo = False
    db.session.commit()
    return "", 204
