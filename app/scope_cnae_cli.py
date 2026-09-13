from __future__ import annotations

from copy import deepcopy
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import click
from flask.cli import AppGroup

from app.cnae import company_cnaes
from app.cnpj import normalize_cnpj
from app.extensions import db
from app.models import Scope


BRASIL_API_CNPJ_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"

scope_cnae_cli = AppGroup(
    "scope-cnae",
    help="Completa e normaliza os códigos CNAE dos escopos.",
)


def fetch_company_by_cnpj(cnpj: str) -> dict:
    normalized = normalize_cnpj(cnpj)
    if not normalized.isdigit() or len(normalized) != 14:
        raise ValueError("A consulta de CNAE exige um CNPJ numérico com 14 dígitos.")
    request = Request(
        BRASIL_API_CNPJ_URL.format(cnpj=normalized),
        headers={
            "Accept": "application/json",
            "User-Agent": "triagem-aduaneira-api/1.0",
        },
    )
    payload = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            break
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == 2:
                raise ValueError(f"BrasilAPI retornou HTTP {exc.code}.") from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise ValueError(f"Falha ao consultar a BrasilAPI: {exc}.") from exc
        time.sleep(2**attempt)
    if not isinstance(payload, dict):
        raise ValueError("A BrasilAPI retornou uma resposta inválida.")
    return payload


def _scope_cnpj(scope: Scope) -> str:
    if scope.client and scope.client.cnpj:
        return normalize_cnpj(scope.client.cnpj)
    return normalize_cnpj(((scope.draft or {}).get("sobreEmpresa") or {}).get("cnpj"))


def _replace_cnaes(
    snapshot: dict | None,
    principal: str,
    secondary: str,
) -> dict | None:
    if snapshot is None:
        return None
    updated = deepcopy(snapshot)
    company = updated.setdefault("sobreEmpresa", {})
    company["cnaePrincipal"] = principal
    company["cnaeSecundario"] = secondary
    return updated


def _scope_result(scope: Scope, principal: str, secondary: str) -> dict:
    company = (scope.draft or {}).get("sobreEmpresa") or {}
    before = {
        "principal": company.get("cnaePrincipal") or "",
        "secondary": company.get("cnaeSecundario") or "",
    }
    after = {"principal": principal, "secondary": secondary}
    return {
        "scope_id": str(scope.id),
        "client_id": str(scope.client_id) if scope.client_id else None,
        "cnpj": _scope_cnpj(scope),
        "status": "ALREADY_CURRENT" if before == after else "UPDATED",
        "before": before,
        "after": after,
    }


@scope_cnae_cli.command("repair")
@click.option("--scope-id", "scope_ids", multiple=True, type=click.UUID)
@click.option("--organization-id", type=click.UUID)
@click.option("--all", "all_scopes", is_flag=True, default=False)
@click.option(
    "--apply",
    is_flag=True,
    default=False,
    help="Confirma a transação. Sem esta opção, o comando executa dry-run.",
)
def repair_scope_cnaes(scope_ids, organization_id, all_scopes: bool, apply: bool):
    """Consulta o CNPJ e completa CNAEs de um, vários ou todos os escopos."""

    if bool(scope_ids) == bool(all_scopes):
        raise click.UsageError("Informe --scope-id (uma ou mais vezes) ou --all.")
    if all_scopes and not organization_id:
        raise click.UsageError("--organization-id é obrigatório com --all.")

    query = Scope.query
    if all_scopes:
        query = query.filter(Scope.organization_id == organization_id)
    else:
        query = query.filter(Scope.id.in_(scope_ids))
        if organization_id:
            query = query.filter(Scope.organization_id == organization_id)

    scopes = query.order_by(Scope.created_at.asc()).all()
    if scope_ids and len(scopes) != len(set(scope_ids)):
        found = {scope.id for scope in scopes}
        missing = [str(scope_id) for scope_id in scope_ids if scope_id not in found]
        raise click.ClickException(f"Escopo(s) não encontrado(s): {', '.join(missing)}")

    cache: dict[str, tuple[str, str] | Exception] = {}
    results: list[dict] = []
    changed_scopes: list[tuple[Scope, str, str]] = []

    for scope in scopes:
        cnpj = _scope_cnpj(scope)
        if not cnpj.isdigit() or len(cnpj) != 14:
            results.append(
                {"scope_id": str(scope.id), "cnpj": "", "status": "INVALID_CNPJ"}
            )
            continue
        if cnpj not in cache:
            try:
                cache[cnpj] = company_cnaes(fetch_company_by_cnpj(cnpj))
            except (ValueError, KeyError) as exc:
                cache[cnpj] = exc
        resolved = cache[cnpj]
        if isinstance(resolved, Exception):
            results.append(
                {
                    "scope_id": str(scope.id),
                    "cnpj": cnpj,
                    "status": "LOOKUP_FAILED",
                    "message": str(resolved),
                }
            )
            continue

        principal, secondary = resolved
        result = _scope_result(scope, principal, secondary)
        results.append(result)
        if result["status"] == "UPDATED":
            changed_scopes.append((scope, principal, secondary))

    if apply:
        try:
            for scope, principal, secondary in changed_scopes:
                scope.draft = _replace_cnaes(scope.draft or {}, principal, secondary)
                if scope.published_snapshot is not None:
                    scope.published_snapshot = _replace_cnaes(
                        scope.published_snapshot, principal, secondary
                    )
                if scope.client:
                    scope.client.cnae_principal = principal
                    scope.client.cnae_secundario = secondary
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    else:
        db.session.rollback()

    click.echo(
        json.dumps(
            {
                "mode": "apply" if apply else "dry-run",
                "selected": len(scopes),
                "updated": len(changed_scopes),
                "failed": sum(
                    item["status"] in {"INVALID_CNPJ", "LOOKUP_FAILED"}
                    for item in results
                ),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
