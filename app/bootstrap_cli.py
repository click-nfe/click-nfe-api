"""One-time initialization of a new production organization."""

import os
import re

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import text

from .auth import ACCESS_TAGS
from .extensions import db
from .models import Organization, User


@click.command("bootstrap-admin")
@click.option("--organization-name", envvar="BOOTSTRAP_ORGANIZATION_NAME", required=True)
@click.option("--organization-slug", envvar="BOOTSTRAP_ORGANIZATION_SLUG", required=True)
@click.option("--name", "admin_name", envvar="BOOTSTRAP_ADMIN_NAME", required=True)
@click.option("--email", envvar="BOOTSTRAP_ADMIN_EMAIL", required=True)
@with_appcontext
def bootstrap_admin(organization_name, organization_slug, admin_name, email):
    """Create the first production organization and administrator exactly once."""
    if current_app.config.get("APP_ENV") != "production":
        raise click.ClickException("Bootstrap disponível somente em produção.")

    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    organization_name = organization_name.strip()
    organization_slug = organization_slug.strip()
    admin_name = admin_name.strip()
    email = email.strip().casefold()

    if not password or len(password) < 16:
        raise click.ClickException("Defina BOOTSTRAP_ADMIN_PASSWORD com pelo menos 16 caracteres.")
    if not 1 <= len(organization_name) <= 255 or not 1 <= len(admin_name) <= 255:
        raise click.ClickException("Nomes devem conter entre 1 e 255 caracteres.")
    if len(organization_slug) > 100 or not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*", organization_slug
    ):
        raise click.ClickException("Slug da organização inválido.")
    if len(email) > 255 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise click.ClickException("E-mail do administrador inválido.")

    try:
        # Serializes concurrent bootstrap attempts on the production PostgreSQL.
        if db.session.get_bind().dialect.name == "postgresql":
            db.session.execute(text("SELECT pg_advisory_xact_lock(812179601418)"))

        if db.session.query(Organization.id).first() or db.session.query(User.id).first():
            raise click.ClickException("Bootstrap recusado: já existem organizações ou usuários.")

        organization = Organization(nome=organization_name, slug=organization_slug, ativo=True)
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            nome=admin_name,
            email=email,
            role="admin",
            access_tags=list(ACCESS_TAGS),
            ativo=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    click.echo("Bootstrap concluído: organização e administrador criados.")
