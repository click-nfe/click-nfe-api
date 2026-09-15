from __future__ import annotations

import re
import unicodedata

import click
from flask import current_app
from flask.cli import AppGroup

from .extensions import db
from .models import Organization, User


dev_cli = AppGroup(
    "dev",
    help="Comandos seguros para preparar o ambiente de desenvolvimento local.",
)


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "organizacao-local"


@dev_cli.command("seed-admin")
@click.option(
    "--organization-name",
    envvar="DEV_ADMIN_ORGANIZATION_NAME",
    required=True,
    help="Nome da organização local.",
)
@click.option(
    "--organization-slug",
    envvar="DEV_ADMIN_ORGANIZATION_SLUG",
    help="Slug da organização. Quando omitido, é gerado a partir do nome.",
)
@click.option(
    "--name",
    "admin_name",
    envvar="DEV_ADMIN_NAME",
    required=True,
    help="Nome do administrador local.",
)
@click.option(
    "--email",
    envvar="DEV_ADMIN_EMAIL",
    required=True,
    help="E-mail usado no login local.",
)
@click.option(
    "--password",
    envvar="DEV_ADMIN_PASSWORD",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    required=True,
    help="Senha local. Prefira o prompt oculto a uma opção na linha de comando.",
)
def seed_admin(
    organization_name: str,
    organization_slug: str | None,
    admin_name: str,
    email: str,
    password: str,
):
    """Cria ou atualiza uma organização e seu administrador local."""
    if current_app.config.get("APP_ENV") != "development":
        raise click.ClickException(
            "Este comando está disponível somente com APP_ENV=development."
        )

    organization_name = organization_name.strip()
    organization_slug = (
        organization_slug.strip() if organization_slug else _slugify(organization_name)
    )
    admin_name = admin_name.strip()
    email = email.strip().casefold()

    if not organization_name or not admin_name:
        raise click.ClickException(
            "Organização e administrador devem ter nomes válidos."
        )
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise click.ClickException("Informe um e-mail válido.")
    if len(password) < 8:
        raise click.ClickException("A senha deve ter pelo menos 8 caracteres.")

    try:
        organization = Organization.query.filter_by(slug=organization_slug).first()
        organization_created = organization is None
        if organization is None:
            organization = Organization(
                nome=organization_name,
                slug=organization_slug,
                ativo=True,
            )
            db.session.add(organization)
            db.session.flush()
        elif organization.nome != organization_name:
            raise click.ClickException(
                f"O slug {organization_slug!r} já pertence a {organization.nome!r}."
            )
        else:
            organization.ativo = True

        user = User.query.filter_by(email=email).first()
        user_created = user is None
        if user is None:
            user = User(
                organization_id=organization.id,
                nome=admin_name,
                email=email,
                role="admin",
                ativo=True,
            )
            db.session.add(user)
        elif user.organization_id != organization.id:
            raise click.ClickException(
                "O e-mail informado já pertence a outra organização."
            )
        else:
            user.nome = admin_name
            user.role = "admin"
            user.ativo = True

        user.set_password(password)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    organization_action = "criada" if organization_created else "atualizada"
    user_action = "criado" if user_created else "atualizado"
    click.echo(f"Organização {organization_slug!r} {organization_action}.")
    click.echo(f"Administrador {email!r} {user_action}; senha não exibida.")
