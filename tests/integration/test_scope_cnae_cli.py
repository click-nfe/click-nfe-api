from datetime import datetime

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, Organization, Scope, ScopeVersion, User


class TestConfig:
    TESTING = True
    SECRET_KEY = "scope-cnae-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False


@pytest.fixture
def cli_context(monkeypatch):
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        organization = Organization(nome="Organização CNAE", slug="org-cnae")
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            nome="Administrador",
            email="admin-cnae@example.invalid",
            role="admin",
            ativo=True,
        )
        user.set_password("test-password")
        db.session.add(user)
        db.session.flush()

        scopes = []
        for index, cnpj in enumerate(("08336506000188", "08266216000105"), start=1):
            client = Client(
                organization_id=organization.id,
                cnpj=cnpj,
                razao_social=f"Cliente {index}",
                cnae_principal="Descrição principal antiga",
                cnae_secundario="Descrição secundária antiga",
            )
            db.session.add(client)
            db.session.flush()
            draft = {
                "sobreEmpresa": {
                    "cnpj": cnpj,
                    "razaoSocial": f"Cliente {index}",
                    "cnaePrincipal": "Descrição principal antiga",
                    "cnaeSecundario": "Descrição secundária antiga",
                }
            }
            scope = Scope(
                organization_id=organization.id,
                client_id=client.id,
                created_by_id=user.id,
                draft=draft,
                published_snapshot=draft.copy(),
                status="published",
                version=2,
            )
            db.session.add(scope)
            db.session.flush()
            db.session.add(
                ScopeVersion(
                    scope_id=scope.id,
                    version_number=2,
                    draft_snapshot=draft,
                    published_snapshot=draft,
                    created_by_id=user.id,
                    created_at=datetime.utcnow(),
                )
            )
            scopes.append(scope)
        db.session.commit()

        monkeypatch.setattr(
            "app.scope_cnae_cli.fetch_company_by_cnpj",
            lambda _cnpj: {
                "cnae_fiscal": 4623109,
                "cnae_fiscal_descricao": "Comércio atacadista de alimentos para animais",
                "cnaes_secundarios": [
                    {"codigo": 4647802, "descricao": "Comércio atacadista de livros"}
                ],
            },
        )

        yield app, organization, scopes
        db.session.remove()
        db.drop_all()


def test_repair_is_dry_run_by_default(cli_context):
    app, _, scopes = cli_context
    result = app.test_cli_runner().invoke(
        args=["scope-cnae", "repair", "--scope-id", str(scopes[0].id)]
    )

    assert result.exit_code == 0, result.output
    assert '"mode": "dry-run"' in result.output
    assert '"status": "UPDATED"' in result.output
    db.session.expire_all()
    assert db.session.get(Scope, scopes[0].id).draft["sobreEmpresa"]["cnaePrincipal"] == (
        "Descrição principal antiga"
    )


def test_repair_applies_to_multiple_scopes_and_preserves_version_history(cli_context):
    app, _, scopes = cli_context
    args = ["scope-cnae", "repair"]
    for scope in scopes:
        args.extend(["--scope-id", str(scope.id)])
    args.append("--apply")

    result = app.test_cli_runner().invoke(args=args)

    assert result.exit_code == 0, result.output
    assert '"selected": 2' in result.output
    assert '"updated": 2' in result.output
    db.session.expire_all()
    for original in scopes:
        scope = db.session.get(Scope, original.id)
        assert scope.draft["sobreEmpresa"]["cnaePrincipal"] == (
            "4623-1/09 - Comércio atacadista de alimentos para animais"
        )
        assert scope.published_snapshot["sobreEmpresa"]["cnaeSecundario"] == (
            "4647-8/02 - Comércio atacadista de livros"
        )
        assert scope.client.cnae_principal.startswith("4623-1/09 - ")
        version = ScopeVersion.query.filter_by(scope_id=scope.id).one()
        assert version.draft_snapshot["sobreEmpresa"]["cnaePrincipal"] == (
            "Descrição principal antiga"
        )


def test_repair_all_requires_and_filters_organization(cli_context):
    app, organization, _ = cli_context
    missing_org = app.test_cli_runner().invoke(
        args=["scope-cnae", "repair", "--all"]
    )
    assert missing_org.exit_code == 2
    assert "--organization-id é obrigatório" in missing_org.output

    result = app.test_cli_runner().invoke(
        args=[
            "scope-cnae",
            "repair",
            "--all",
            "--organization-id",
            str(organization.id),
        ]
    )
    assert result.exit_code == 0, result.output
    assert '"selected": 2' in result.output
