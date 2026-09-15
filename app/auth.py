from datetime import datetime, timedelta
from functools import wraps
from uuid import UUID

import jwt
from flask import current_app, g, jsonify, request

from .extensions import db
from .models import RefreshToken, User


ADMIN_ROLE = "admin"


def serialize_identity(identity) -> dict:
    return {
        "id": str(identity.id),
        "nome": identity.nome,
        "email": identity.email,
        "role": identity.role,
        "setor": identity.setor,
        "tipo": "user",
        "organizationId": str(identity.organization_id) if identity.organization_id else None,
    }


def resolve_identity(principal_id: str):
    try:
        identity_id = UUID(str(principal_id))
    except (TypeError, ValueError, AttributeError):
        return None
    return db.session.get(User, identity_id)


def _jwt_payload(identity, principal_type: str, token_type: str, expires_in: int) -> dict:
    now = datetime.utcnow()
    return {
        "sub": str(identity.id),
        "email": identity.email,
        "role": ADMIN_ROLE if principal_type == "admin" else identity.role,
        "principal_type": principal_type,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }


def generate_tokens(identity, principal_type: str = "user") -> dict:
    access_expires = current_app.config["JWT_ACCESS_EXPIRES_SECONDS"]
    refresh_expires = current_app.config["JWT_REFRESH_EXPIRES_SECONDS"]

    access_token = jwt.encode(
        _jwt_payload(identity, principal_type, "access", access_expires),
        current_app.config["SECRET_KEY"],
        algorithm="HS256",
    )
    refresh_token = jwt.encode(
        _jwt_payload(identity, principal_type, "refresh", refresh_expires),
        current_app.config["SECRET_KEY"],
        algorithm="HS256",
    )

    db.session.add(
        RefreshToken(
            user_id=identity.id,
            token=refresh_token,
            expires_at=datetime.utcnow() + timedelta(seconds=refresh_expires),
        )
    )
    db.session.commit()

    return {"accessToken": access_token, "refreshToken": refresh_token, "expiresIn": access_expires}


def decode_token(token: str) -> dict:
    return jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])


def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "Missing Bearer token"}), 401

        token = header.split(" ", 1)[1].strip()
        try:
            payload = decode_token(token)
        except jwt.PyJWTError:
            return jsonify({"error": "Invalid token"}), 401

        if payload.get("type") != "access":
            return jsonify({"error": "Invalid access token"}), 401

        identity = resolve_identity(payload["sub"])
        if not identity or not identity.ativo:
            return jsonify({"error": "User not found or inactive"}), 401
        if not identity.organization_id or not identity.organization:
            return jsonify(
                {
                    "error": "organization_required",
                    "message": "O usuário precisa estar vinculado a uma organização.",
                }
            ), 403
        if not identity.organization.ativo:
            return jsonify(
                {
                    "error": "organization_inactive",
                    "message": "A organização do usuário está inativa.",
                }
            ), 403

        g.current_user = identity
        g.current_user_type = "admin" if identity.role == ADMIN_ROLE else "user"
        g.current_identity = serialize_identity(identity)
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    @auth_required
    def wrapper(*args, **kwargs):
        if g.current_user.role != ADMIN_ROLE:
            return jsonify({"error": "Forbidden"}), 403
        return fn(*args, **kwargs)

    return wrapper


def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @auth_required
        def wrapper(*args, **kwargs):
            if g.current_user.role == ADMIN_ROLE:
                return fn(*args, **kwargs)
            if g.current_user.role not in roles:
                return jsonify({"error": "Forbidden"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
