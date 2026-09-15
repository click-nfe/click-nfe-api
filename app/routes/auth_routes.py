from datetime import datetime
import re

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from ..auth import auth_required, decode_token, generate_tokens, serialize_identity
from ..extensions import db
from ..models import Organization, RefreshToken, User
from ..schemas import LoginSchema, RefreshSchema, RegisterSchema

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
login_schema = LoginSchema()
refresh_schema = RefreshSchema()
register_schema = RegisterSchema()


def _slugify(text: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return base or "org"


@auth_bp.post("/register")
def register():
    payload = register_schema.load(request.get_json(force=True))

    if User.query.filter_by(email=payload["email"]).first():
        return jsonify({"error": "Email já cadastrado"}), 409

    slug = payload.get("organization_slug") or _slugify(payload["organization_nome"])
    organization = Organization(
        nome=payload["organization_nome"],
        slug=slug,
        cnpj=payload.get("organization_cnpj"),
    )
    try:
        db.session.add(organization)
        db.session.flush()

        user = User(
            nome=payload["nome"],
            email=payload["email"],
            role="admin",
            setor=payload.get("setor"),
            organization_id=organization.id,
        )
        user.set_password(payload["password"])
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Conflito ao criar usuário/organização"}), 409

    return jsonify({"user": serialize_identity(user), "tokens": generate_tokens(user)}), 201


@auth_bp.post("/login")
def login():
    payload = login_schema.load(request.get_json(force=True))

    user = User.query.filter_by(email=payload["email"], ativo=True).first()
    if (
        not user
        or not user.check_password(payload["password"])
        or not user.organization
        or not user.organization.ativo
    ):
        return jsonify({"error": "Invalid credentials"}), 401

    return jsonify({"user": serialize_identity(user), "tokens": generate_tokens(user)})


@auth_bp.post("/refresh")
def refresh():
    payload = refresh_schema.load(request.get_json(force=True))
    token = payload["refreshToken"]

    try:
        decoded = decode_token(token)
    except Exception:
        return jsonify({"error": "Invalid refresh token"}), 401

    if decoded.get("type") != "refresh":
        return jsonify({"error": "Invalid token type"}), 401

    persisted = RefreshToken.query.filter_by(token=token, revoked=False).first()
    if not persisted or persisted.expires_at < datetime.utcnow():
        return jsonify({"error": "Refresh token expired or revoked"}), 401

    identity = persisted.user
    if (
        not identity
        or not identity.ativo
        or not identity.organization
        or not identity.organization.ativo
    ):
        return jsonify({"error": "Invalid user"}), 401

    persisted.revoked = True
    db.session.commit()

    return jsonify({"tokens": generate_tokens(identity)})


@auth_bp.post("/logout")
@auth_required
def logout():
    db.session.query(RefreshToken).filter_by(
        user_id=g.current_user.id,
        revoked=False,
    ).update({"revoked": True})
    db.session.commit()
    return "", 204


@auth_bp.get("/me")
@auth_required
def me():
    return jsonify(g.current_identity)
