from __future__ import annotations

import json
from pathlib import Path

import click
from flask.cli import AppGroup

from app.services.preposto_catalog_import import import_preposto_catalog_2025


preposto_catalog_cli = AppGroup(
    "preposto-catalog",
    help="Valida e importa catálogos versionados de prepostos.",
)


@preposto_catalog_cli.command("import-2025")
@click.argument(
    "source_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--organization-id", required=True, type=click.UUID)
@click.option(
    "--apply",
    is_flag=True,
    default=False,
    help="Confirma a transação. Sem esta opção, o comando executa dry-run.",
)
def import_2025(source_file: Path, organization_id, apply: bool):
    """Importa somente os registros aprovados da Relação 2025."""

    try:
        result = import_preposto_catalog_2025(
            organization_id,
            source_path=source_file,
            apply=apply,
        )
    except (ValueError, KeyError) as error:
        raise click.ClickException(str(error)) from error

    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
