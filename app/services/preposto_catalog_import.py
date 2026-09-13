from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID

from app.extensions import db
from app.models import (
    Organization,
    Preposto,
    PrepostoContato,
    PrepostoCredenciado,
    PrepostoCredenciadoVinculo,
    PrepostoLocalidade,
    PrepostoTarifa,
)


def _decimal_or_none(value):
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _normalized(value: str | None) -> str:
    return (value or "").strip().lower()


def _load_catalog(source_path: Path) -> dict:
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    required = {
        "metadata",
        "providers",
        "contacts",
        "localities",
        "tariffs",
        "credentials",
        "bindings",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError(f"Catálogo incompleto; chaves ausentes: {', '.join(missing)}.")
    return payload


def _find_provider(organization_id: UUID, item: dict) -> Preposto | None:
    existing_id = item.get("existing_id")
    if existing_id:
        provider = db.session.get(Preposto, UUID(existing_id))
        if provider and provider.organization_id == organization_id:
            return provider

    names = {
        _normalized(item.get("name")),
        _normalized(item.get("existing_name")),
    }
    names.discard("")
    return (
        Preposto.query.filter(Preposto.organization_id == organization_id)
        .filter(db.func.lower(Preposto.nome).in_(names))
        .first()
    )


def _find_contact(provider_id: UUID, item: dict) -> PrepostoContato | None:
    if item.get("email"):
        contact = (
            PrepostoContato.query.filter(PrepostoContato.preposto_id == provider_id)
            .filter(
                db.func.lower(PrepostoContato.email)
                == item["email"].strip().lower()
            )
            .first()
        )
        if contact:
            return contact

    return (
        PrepostoContato.query.filter(PrepostoContato.preposto_id == provider_id)
        .filter(db.func.lower(PrepostoContato.nome) == item["name"].strip().lower())
        .first()
    )


def _find_locality(provider_id: UUID, item: dict) -> PrepostoLocalidade | None:
    query = PrepostoLocalidade.query.filter(
        PrepostoLocalidade.preposto_id == provider_id,
        db.func.lower(PrepostoLocalidade.cidade) == item["city"].strip().lower(),
    )
    if item.get("description"):
        exact = query.filter(
            db.func.lower(PrepostoLocalidade.descricao_local)
            == item["description"].strip().lower()
        ).first()
        if exact:
            return exact
    return query.first()


def import_preposto_catalog_2025(
    organization_id: str | UUID,
    *,
    source_path: Path,
    apply: bool = False,
) -> dict:
    """Aplica o catálogo aprovado de forma idempotente.

    Por padrão toda a sincronização roda em transação e termina em rollback. O
    chamador precisa passar ``apply=True`` para efetivar a carga.
    """

    organization_uuid = (
        organization_id
        if isinstance(organization_id, UUID)
        else UUID(str(organization_id))
    )
    organization = db.session.get(Organization, organization_uuid)
    if not organization:
        raise ValueError(f"Organização {organization_uuid} não encontrada.")

    catalog = _load_catalog(source_path)
    stats = {
        entity: {"created": 0, "updated": 0, "removed": 0}
        for entity in (
            "providers",
            "contacts",
            "localities",
            "tariffs",
            "credentials",
            "bindings",
        )
    }

    providers_by_key: dict[str, Preposto] = {}
    localities_by_key: dict[str, PrepostoLocalidade] = {}
    credentials_by_key: dict[str, PrepostoCredenciado] = {}

    try:
        for item in catalog["providers"]:
            provider = _find_provider(organization_uuid, item)
            if provider is None:
                provider = Preposto(organization_id=organization_uuid)
                db.session.add(provider)
                stats["providers"]["created"] += 1
            else:
                stats["providers"]["updated"] += 1

            provider.nome = item["name"].strip()
            provider.ativo = item.get("active", True)
            provider.observacoes = item.get("notes")
            db.session.flush()
            providers_by_key[item["key"]] = provider

        contacts_by_provider: dict[str, list[dict]] = defaultdict(list)
        for item in catalog["contacts"]:
            contacts_by_provider[item["provider_key"]].append(item)

        for provider_key, desired_contacts in contacts_by_provider.items():
            provider = providers_by_key[provider_key]
            kept_contact_ids = set()
            for item in desired_contacts:
                contact = _find_contact(provider.id, item)
                if contact is None:
                    contact = PrepostoContato(preposto_id=provider.id)
                    db.session.add(contact)
                    stats["contacts"]["created"] += 1
                else:
                    stats["contacts"]["updated"] += 1

                contact.nome = item["name"].strip()
                contact.email = item.get("email")
                contact.telefone = item.get("phone")
                contact.whatsapp = item.get("whatsapp")
                contact.principal = item.get("primary", False)
                db.session.flush()
                kept_contact_ids.add(contact.id)

            stale_contacts = PrepostoContato.query.filter(
                PrepostoContato.preposto_id == provider.id,
                PrepostoContato.id.notin_(kept_contact_ids),
            ).all()
            for contact in stale_contacts:
                db.session.delete(contact)
                stats["contacts"]["removed"] += 1

        for item in catalog["localities"]:
            provider = providers_by_key[item["provider_key"]]
            locality = _find_locality(provider.id, item)
            if locality is None:
                locality = PrepostoLocalidade(preposto_id=provider.id)
                db.session.add(locality)
                stats["localities"]["created"] += 1
            else:
                stats["localities"]["updated"] += 1

            locality.cidade = item["city"].strip()
            locality.uf = item.get("state")
            locality.descricao_local = item.get("description")
            locality.tipo_local = item.get("location_type")
            locality.atende_importacao = item.get("serves_import", False)
            locality.atende_exportacao = item.get("serves_export", False)
            locality.valor_importacao = _decimal_or_none(item.get("import_value"))
            locality.valor_exportacao = _decimal_or_none(item.get("export_value"))
            locality.valor_importacao_descricao = item.get(
                "import_value_description"
            )
            locality.valor_exportacao_descricao = item.get(
                "export_value_description"
            )
            locality.moeda = item.get("currency") or "BRL"
            locality.observacoes = item.get("notes")
            db.session.flush()
            localities_by_key[item["key"]] = locality

        desired_tariff_codes: dict[UUID, set[str]] = defaultdict(set)
        for item in catalog["tariffs"]:
            operations = (
                ("IMPORTACAO", "EXPORTACAO")
                if item["operation"] == "AMBAS"
                else (item["operation"],)
            )
            for locality_key in item["locality_keys"]:
                locality = localities_by_key[locality_key]
                for operation in operations:
                    code = f"{item['key']}:{operation}"
                    desired_tariff_codes[locality.id].add(code)
                    tariff = PrepostoTarifa.query.filter_by(
                        localidade_id=locality.id,
                        codigo=code,
                    ).first()
                    if tariff is None:
                        tariff = PrepostoTarifa(
                            localidade_id=locality.id,
                            codigo=code,
                        )
                        db.session.add(tariff)
                        stats["tariffs"]["created"] += 1
                    else:
                        stats["tariffs"]["updated"] += 1

                    tariff.operacao = operation
                    tariff.tipo = item["tariff_type"]
                    tariff.valor = _decimal_or_none(item.get("value"))
                    tariff.valor_descricao = item.get("value_description")
                    tariff.condicao = item.get("condition")
                    tariff.principal = item.get("primary", False)
                    tariff.moeda = item.get("currency") or "BRL"
                    tariff.ativo = item.get("active", True)
                    tariff.observacoes = item.get("notes")

        for locality_id, desired_codes in desired_tariff_codes.items():
            stale_tariffs = PrepostoTarifa.query.filter(
                PrepostoTarifa.localidade_id == locality_id,
                PrepostoTarifa.codigo.notin_(desired_codes),
                PrepostoTarifa.ativo.is_(True),
            ).all()
            for tariff in stale_tariffs:
                tariff.ativo = False
                stats["tariffs"]["removed"] += 1

        for item in catalog["credentials"]:
            credential = PrepostoCredenciado.query.filter_by(
                organization_id=organization_uuid,
                cpf=item["cpf"],
            ).first()
            if credential is None:
                credential = PrepostoCredenciado(
                    organization_id=organization_uuid,
                    cpf=item["cpf"],
                )
                db.session.add(credential)
                stats["credentials"]["created"] += 1
            else:
                stats["credentials"]["updated"] += 1

            credential.nome = item["name"].strip()
            credential.registro_rfb = item.get("rfb_registration")
            credential.categoria = item["category"]
            credential.ativo = item.get("active", True)
            db.session.flush()
            credentials_by_key[item["key"]] = credential

        for item in catalog["bindings"]:
            credential = credentials_by_key[item["credential_key"]]
            provider = providers_by_key[item["provider_key"]]
            for locality_key in item["locality_keys"]:
                locality = localities_by_key[locality_key]
                binding = PrepostoCredenciadoVinculo.query.filter_by(
                    credenciado_id=credential.id,
                    preposto_id=provider.id,
                    localidade_id=locality.id,
                ).first()
                if binding is None:
                    binding = PrepostoCredenciadoVinculo(
                        credenciado_id=credential.id,
                        preposto_id=provider.id,
                        localidade_id=locality.id,
                    )
                    db.session.add(binding)
                    stats["bindings"]["created"] += 1
                else:
                    stats["bindings"]["updated"] += 1
                binding.ativo = item.get("active", True)
                binding.observacoes = item.get("notes")

        db.session.flush()
        if apply:
            db.session.commit()
        else:
            db.session.rollback()
    except Exception:
        db.session.rollback()
        raise

    return {
        "applied": apply,
        "catalog": catalog["metadata"]["catalog"],
        "organization_id": str(organization_uuid),
        "stats": stats,
        "excluded_sections": catalog["metadata"]["excluded_sections"],
    }
