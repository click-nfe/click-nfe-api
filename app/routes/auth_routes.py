from datetime import datetime
import re

from flask import Blueprint, g, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..auth import ACCESS_TAGS, auth_required, decode_token, generate_tokens, serialize_identity
from ..extensions import db
from ..models import Organization, RefreshToken, User
from ..schemas import LoginSchema, RefreshSchema

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
login_schema = LoginSchema()
refresh_schema = RefreshSchema()


@auth_bp.post("/register")
def register():
    """Allow a new organization to create its first administrator."""
    if request.content_length is not None and request.content_length > 4096:
        return jsonify({"error": "validation_error", "message": "Cadastro muito grande."}), 413

    data = request.get_json(silent=True)
    expected = {"organization_name", "organization_slug", "name", "email", "password"}
    if not isinstance(data, dict) or set(data) != expected:
        return jsonify({"error": "validation_error", "message": "Preencha todos os campos do cadastro."}), 400

    if any(not isinstance(data[key], str) for key in expected):
        return jsonify({"error": "validation_error", "message": "Campos inválidos."}), 400

    org_name = data["organization_name"].strip()
    slug = data["organization_slug"].strip()
    name = data["name"].strip()
    email = data["email"].strip().casefold()
    password = data["password"]

    if not 1 <= len(org_name) <= 255 or not 1 <= len(name) <= 255:
        return jsonify({"error": "validation_error", "message": "Nomes devem conter entre 1 e 255 caracteres."}), 400
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 100:
        return jsonify({"error": "validation_error", "message": "Slug inválido. Use letras minúsculas, números e hífens."}), 400
    if len(email) > 255 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"error": "validation_error", "message": "E-mail inválido."}), 400
    if not 16 <= len(password) <= 128:
        return jsonify({"error": "validation_error", "message": "A senha deve conter entre 16 e 128 caracteres."}), 400

    try:
        # Serializes the case-insensitive name check across Cloud Run instances.
        if db.session.get_bind().dialect.name == "postgresql":
            db.session.execute(text("SELECT pg_advisory_xact_lock(812179601419)"))

        if Organization.query.filter(db.func.lower(Organization.nome) == org_name.lower()).first():
            db.session.rollback()
            return jsonify({"error": "organization_name_in_use", "message": "Nome da organização já está em uso."}), 409
        if Organization.query.filter_by(slug=slug).first():
            db.session.rollback()
            return jsonify({"error": "organization_slug_in_use", "message": "Slug já está em uso."}), 409
        if User.query.filter_by(email=email).first():
            db.session.rollback()
            return jsonify({"error": "email_in_use", "message": "E-mail já está em uso."}), 409

        organization = Organization(nome=org_name, slug=slug, email=email, ativo=True)
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            nome=name,
            email=email,
            role="admin",
            access_tags=list(ACCESS_TAGS),
            ativo=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "registration_conflict", "message": "Organização, slug ou e-mail já cadastrado."}), 409

    return jsonify({"organization": {"id": str(organization.id), "slug": slug}, "email": email}), 201


@auth_bp.post("/login")
def login():
    payload = login_schema.load(request.get_json(force=True))

    user = User.query.filter_by(
        email=payload["email"].strip().casefold(),
        ativo=True,
    ).first()
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
