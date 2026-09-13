from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import and_, distinct, func

from app.models.scope import ScopeTemplate
from app.cnae import normalize_cnae, normalize_cnae_list

from ..extensions import db
from ..models import (
    Client,
    OrganizationSetting,
    Preposto,
    Scope,
    ScopeAssignment,
    ScopePreposto,
    ScopeService,
    ServiceCatalog,
    User,
)
from ..scope_defaults import apply_admin_defaults, build_default_scope_draft, merge_scope_draft


@dataclass
class ScopeSyncResult:
    scope_id: str
    already_synced: bool
    changed: bool
    dry_run: bool
    missing: dict[str, list[str]] = field(default_factory=dict)
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    deactivated: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scopeId": self.scope_id,
            "alreadySynced": self.already_synced,
            "changed": self.changed,
            "dryRun": self.dry_run,
            "missing": self.missing,
            "created": self.created,
            "updated": self.updated,
            "deactivated": self.deactivated,
        }


class ScopeDataProcessor:
    """Centraliza as regras de normalização e sincronização do draft de Scope.

    Uso esperado nos endpoints:
        processor = ScopeDataProcessor(current_user=g.current_user)
        draft = processor.normalize_draft(payload)
        processor.apply_draft_to_scope(scope, draft)
        processor.sync_scope(scope)

    A classe mantém Scope.draft como snapshot editável, mas materializa os dados
    consultáveis em tabelas relacionais: Client, ScopeAssignment, ScopeService e
    ScopePreposto.
    """

    DEFAULT_SETTINGS = {
        "salarioMinimoVigente": 0,
        "dadosBancariosCasco": {"banco": "", "agencia": "", "conta": ""},
    }

    MANAGED_ASSIGNMENT_ROLES = {
        "RESPONSAVEL_COMERCIAL",
        "ANALISTA_DA_IMPORT",
        "ANALISTA_AE_IMPORT",
        "ANALISTA_DA_EXPORT",
        "ANALISTA_AE_EXPORT",
    }

    BULK_ASSIGNMENT_GROUPS = {
        "responsavel_comercial": {
            "scope_field": "responsible_user_id",
            "roles": ("RESPONSAVEL_COMERCIAL",),
            "draft_path": ("sobreEmpresa", "responsavelComercial"),
        },
        "analista_da": {
            "roles": ("ANALISTA_DA_IMPORT", "ANALISTA_DA_EXPORT"),
            "draft_paths": (
                ("operacao", "importacao", "analistaDA"),
                ("operacao", "exportacao", "analistaDA"),
            ),
        },
        "analista_ae": {
            "roles": ("ANALISTA_AE_IMPORT", "ANALISTA_AE_EXPORT"),
            "draft_paths": (
                ("operacao", "importacao", "analistaAE"),
                ("operacao", "exportacao", "analistaAE"),
            ),
        },
    }

    SERVICE_NAME_MAP = {
        "despachoAduaneiroImportacao": "Despacho Aduaneiro Importação",
        "emissaoLiLpco": "Emissão LI/LPCO",
        "cadastroCatalogoProdutos": "Cadastro Catálogo de Produtos",
        "assessoria": "Assessoria",
        "freteInternacional": "Frete Internacional",
        "seguroInternacional": "Seguro Internacional",
        "freteRodoviario": "Frete Rodoviário",
        "emissaoNfe": "Emissão NF-e",
        "despachoAduaneiroExportacao": "Despacho Aduaneiro Exportação",
        "certificadoOrigem": "Certificado de Origem",
        "certificadoFitossanitario": "Certificado Fitossanitário",
        "outrosCertificados": "Outros Certificados",
        "preposto": "Preposto",
    }

    def __init__(self, current_user=None):
        self.current_user = current_user

    # ---------------------------------------------------------------------
    # Helpers gerais
    # ---------------------------------------------------------------------
    @property
    def organization_id(self):
        return getattr(self.current_user, "organization_id", None)

    @property
    def user_id(self):
        return getattr(self.current_user, "id", None)

    def get_admin_settings(self) -> dict:
        if not self.organization_id:
            return self.DEFAULT_SETTINGS.copy()

        row = OrganizationSetting.query.filter_by(
            organization_id=self.organization_id,
            key="scope_fixed_info",
        ).first()
        return row.value_json if row and row.value_json else self.DEFAULT_SETTINGS.copy()

    def normalize_draft(self, draft: dict | None) -> dict:
        normalized = apply_admin_defaults(
            merge_scope_draft(build_default_scope_draft(), draft or {}),
            self.get_admin_settings(),
        )
        company = normalized.get("sobreEmpresa") or {}
        company["cnaePrincipal"] = normalize_cnae(company.get("cnaePrincipal"))
        company["cnaeSecundario"] = normalize_cnae_list(company.get("cnaeSecundario"))
        return normalized

    def scope_query_for_current_user(self):
        query = Scope.query
        if self.organization_id:
            query = query.filter(Scope.organization_id == self.organization_id)
        return query
    
    def template_query_for_current_user(self):
        query = ScopeTemplate.query
        if self.organization_id:
            query = query.filter(ScopeTemplate.organization_id == self.organization_id)
        return query

    def build_scope_summary(self, scope: Scope) -> dict:
        return {
            "id": str(scope.id),
            "status": scope.status,
            "version": scope.version,
            "updated_at": scope.updated_at,
            "last_published_at": scope.last_published_at,
            "client_id": str(scope.client_id) if scope.client_id else None,
            "client_cnpj": scope.client.cnpj if scope.client else None,
            "client_razao_social": scope.client.razao_social if scope.client else None,
            "client_nome_resumido": scope.client.nome_resumido if scope.client else None,
            "responsible_user_id": str(scope.responsible_user_id) if scope.responsible_user_id else None,
            "responsible_user_nome": scope.responsible_user.nome if scope.responsible_user else None,
        }

    def apply_draft_to_scope(self, scope: Scope, normalized_draft: dict) -> Scope:
        sobre_empresa = normalized_draft.get("sobreEmpresa") or {}
        responsible_user_id = (
            sobre_empresa.get("responsavelComercial")
            or sobre_empresa.get("responsavelComercialId")
        )

        scope.draft = normalized_draft
        scope.responsible_user_id = responsible_user_id or None
        return scope

    # ---------------------------------------------------------------------
    # Client
    # ---------------------------------------------------------------------
    def upsert_client_from_draft(self, scope: Scope, normalized_draft: dict) -> Client | None:
        sobre_empresa = normalized_draft.get("sobreEmpresa") or {}
        cnpj = (sobre_empresa.get("cnpj") or "").strip()
        razao_social = (sobre_empresa.get("razaoSocial") or "").strip()

        if not cnpj or not razao_social or not scope.organization_id:
            return None

        client = Client.query.filter_by(
            organization_id=scope.organization_id,
            cnpj=cnpj,
        ).first()

        if not client:
            client = Client(
                organization_id=scope.organization_id,
                cnpj=cnpj,
                razao_social=razao_social,
            )
            db.session.add(client)

        client.razao_social = razao_social
        client.nome_resumido = sobre_empresa.get("nomeResumido")
        client.inscricao_estadual = sobre_empresa.get("inscricaoEstadual")
        client.inscricao_municipal = sobre_empresa.get("inscricaoMunicipal")
        client.endereco_completo_escritorio = sobre_empresa.get("enderecoCompletoEscritorio")
        client.endereco_completo_armazem = sobre_empresa.get("enderecoCompletoArmazem")
        client.cnae_principal = sobre_empresa.get("cnaePrincipal")
        client.cnae_secundario = sobre_empresa.get("cnaeSecundario")
        client.regime_tributacao = sobre_empresa.get("regimeTributacao")

        scope.client = client
        return client

    # ---------------------------------------------------------------------
    # Assignments
    # ---------------------------------------------------------------------
    def _extract_assignment_targets(self, draft: dict) -> list[tuple[str, str]]:
        targets: list[tuple[str, str]] = []
        sobre_empresa = draft.get("sobreEmpresa") or {}
        operacao = draft.get("operacao") or {}

        responsavel = (
            sobre_empresa.get("responsavelComercial")
            or sobre_empresa.get("responsavelComercialId")
        )
        if responsavel:
            targets.append((responsavel, "RESPONSAVEL_COMERCIAL"))

        importacao = operacao.get("importacao") or {}
        exportacao = operacao.get("exportacao") or {}

        for user_id in importacao.get("analistaDA") or []:
            if user_id:
                targets.append((user_id, "ANALISTA_DA_IMPORT"))

        for user_id in importacao.get("analistaAE") or []:
            if user_id:
                targets.append((user_id, "ANALISTA_AE_IMPORT"))

        for user_id in exportacao.get("analistaDA") or []:
            if user_id:
                targets.append((user_id, "ANALISTA_DA_EXPORT"))

        for user_id in exportacao.get("analistaAE") or []:
            if user_id:
                targets.append((user_id, "ANALISTA_AE_EXPORT"))

        # Remove duplicidades mantendo ordem.
        deduped = []
        seen = set()
        for user_id, role in targets:
            key = (str(user_id), role)
            if key not in seen:
                deduped.append((str(user_id), role))
                seen.add(key)
        return deduped

    def sync_assignments_from_draft(self, scope: Scope, draft: dict) -> dict[str, int]:
        now = datetime.utcnow()
        desired = self._extract_assignment_targets(draft)
        desired_keys = {(user_id, role) for user_id, role in desired}
        counters = {"created": 0, "updated": 0, "deactivated": 0}

        active_assignments = ScopeAssignment.query.filter(
            ScopeAssignment.scope_id == scope.id,
            ScopeAssignment.role.in_(self.MANAGED_ASSIGNMENT_ROLES),
            ScopeAssignment.active.is_(True),
        ).all()

        for assignment in active_assignments:
            key = (str(assignment.user_id), assignment.role)
            if key not in desired_keys:
                assignment.active = False
                assignment.ends_at = now
                counters["deactivated"] += 1

        for user_id, role in desired:
            existing = ScopeAssignment.query.filter_by(
                scope_id=scope.id,
                user_id=user_id,
                role=role,
                active=True,
            ).first()
            if existing:
                counters["updated"] += 1
                continue

            db.session.add(
                ScopeAssignment(
                    scope_id=scope.id,
                    user_id=user_id,
                    role=role,
                    active=True,
                    starts_at=now,
                )
            )
            counters["created"] += 1

        return counters

    # ---------------------------------------------------------------------
    # Services
    # ---------------------------------------------------------------------
    def _operation_type_from_group(self, group_key: str) -> str:
        if group_key == "importacao":
            return "IMPORTACAO"
        if group_key == "exportacao":
            return "EXPORTACAO"
        return "AMBOS"

    def _catalog_code(self, operation_type: str, service_code: str) -> str:
        return f"{operation_type}:{service_code}"

    def _service_name(self, operation_type: str, service_code: str) -> str:
        base_name = self.SERVICE_NAME_MAP.get(service_code, service_code)
        suffix = "Importação" if operation_type == "IMPORTACAO" else "Exportação" if operation_type == "EXPORTACAO" else "Ambos"
        return f"{base_name} - {suffix}"

    def _get_or_create_service_catalog(
        self,
        organization_id,
        operation_type: str,
        service_code: str,
    ) -> ServiceCatalog:
        code = self._catalog_code(operation_type, service_code)
        service = ServiceCatalog.query.filter_by(
            organization_id=organization_id,
            code=code,
        ).first()

        if service:
            service.nome = service.nome or self._service_name(operation_type, service_code)
            service.operation_type = service.operation_type or operation_type
            return service

        service = ServiceCatalog(
            organization_id=organization_id,
            code=code,
            nome=self._service_name(operation_type, service_code),
            operation_type=operation_type,
            ativo=True,
        )
        db.session.add(service)
        db.session.flush()
        return service

    def _to_decimal(self, value) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _extract_service_amount(self, payload: dict) -> Decimal | None:
        if not isinstance(payload, dict):
            return None
        return self._to_decimal(payload.get("valor")) or self._to_decimal(payload.get("valorMinimo"))

    def _extract_pricing_type(self, payload: dict) -> str | None:
        if not isinstance(payload, dict):
            return None
        if payload.get("tipoValor"):
            return payload.get("tipoValor")
        if payload.get("modalidade"):
            return payload.get("modalidade")
        if payload.get("percentualSobreCfr") not in (None, ""):
            return "PERCENTUAL"
        if payload.get("valor") not in (None, "") or payload.get("valorMinimo") not in (None, ""):
            return "FIXO"
        return None

    def _is_service_enabled_payload(self, payload: Any) -> bool:
        """Retorna True apenas para serviços efetivamente selecionados no draft.

        O draft normalizado contém a grade completa de serviços possíveis, inclusive
        serviços com habilitado=false. Para métricas e sincronização relacional,
        materializamos somente o que foi escolhido no escopo.
        """
        return isinstance(payload, dict) and payload.get("habilitado") is True

    def _iter_enabled_service_payloads(self, draft: dict):
        """Itera apenas serviços habilitados em importação/exportação."""
        servicos = draft.get("servicos") or {}

        for group_key in ["importacao", "exportacao"]:
            group_services = servicos.get(group_key) or {}
            operation_type = self._operation_type_from_group(group_key)

            if not isinstance(group_services, dict):
                continue

            for raw_service_code, payload in group_services.items():
                if not self._is_service_enabled_payload(payload):
                    continue
                yield operation_type, raw_service_code, payload

    def sync_services_from_draft(self, scope: Scope, draft: dict) -> dict[str, int]:
        """Materializa em ScopeService apenas serviços habilitados no draft.

        Serviços presentes no template com habilitado=false não são criados como
        relacionamento. Caso já exista uma linha antiga para serviço desabilitado,
        ela é removida para manter ScopeService como tabela de serviços contratados.
        """
        seen_catalog_ids = set()
        counters = {"created": 0, "updated": 0, "deleted": 0}

        for operation_type, raw_service_code, payload in self._iter_enabled_service_payloads(draft):
            catalog = self._get_or_create_service_catalog(
                organization_id=scope.organization_id,
                operation_type=operation_type,
                service_code=raw_service_code,
            )
            seen_catalog_ids.add(catalog.id)

            scope_service = ScopeService.query.filter_by(
                scope_id=scope.id,
                service_catalog_id=catalog.id,
            ).first()

            if not scope_service:
                scope_service = ScopeService(
                    scope_id=scope.id,
                    service_catalog_id=catalog.id,
                )
                db.session.add(scope_service)
                counters["created"] += 1
            else:
                counters["updated"] += 1

            scope_service.enabled = True
            scope_service.pricing_type = self._extract_pricing_type(payload)
            scope_service.amount = self._extract_service_amount(payload)
            scope_service.currency = payload.get("moeda") or "BRL"
            scope_service.responsible_user_id = payload.get("responsavel") or None
            scope_service.extra_data = payload

        existing_query = ScopeService.query.filter(ScopeService.scope_id == scope.id)
        if seen_catalog_ids:
            existing_query = existing_query.filter(~ScopeService.service_catalog_id.in_(seen_catalog_ids))

        deleted = existing_query.delete(synchronize_session=False)
        counters["deleted"] += deleted

        return counters
    
    def _to_bool_or_none(self, value):
        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            normalized = value.strip().upper()

            if normalized in ("SIM", "S", "TRUE", "1", "YES", "Y"):
                return True

            if normalized in ("NAO", "NÃO", "N", "FALSE", "0", "NO"):
                return False

            if normalized == "":
                return None

        if isinstance(value, int):
            if value == 1:
                return True
            if value == 0:
                return False

        return None

    # ---------------------------------------------------------------------
    # Prepostos
    # ---------------------------------------------------------------------
    def _is_uuid_like(self, value) -> bool:
        if isinstance(value, UUID):
            return True
        if not isinstance(value, str):
            return False
        try:
            UUID(value)
            return True
        except (ValueError, TypeError):
            return False

    def _extract_preposto_id(self, preposto_payload: dict) -> str | UUID | None:
        if not isinstance(preposto_payload, dict):
            return None

        selected = preposto_payload.get("prepostoSelecionado")

        if self._is_uuid_like(selected):
            return selected

        if isinstance(selected, dict):
            for key in ("id", "preposto_id", "prepostoId"):
                candidate = selected.get(key)
                if self._is_uuid_like(candidate):
                    return candidate

        for key in ("preposto_id", "prepostoId"):
            candidate = preposto_payload.get(key)
            if self._is_uuid_like(candidate):
                return candidate

        return None

    def _is_manual_preposto_payload(self, preposto_payload: dict) -> bool:
        if not isinstance(preposto_payload, dict):
            return False

        selected = preposto_payload.get("prepostoSelecionado")
        return isinstance(selected, dict) and not self._extract_preposto_id(preposto_payload)

    def sync_prepostos_from_draft(self, scope: Scope, draft: dict) -> dict[str, int]:
        """Sincroniza vínculos ScopePreposto apenas quando houver preposto_id válido.

        Prepostos manuais sem UUID não podem virar FK para prepostos.id. Esses
        casos são reportados no dry_run por get_sync_missing, mas não quebram a
        sincronização real.
        """
        servicos = draft.get("servicos") or {}
        counters = {"created": 0, "updated": 0, "disabled": 0}
        seen_keys = set()

        for group_key in ["importacao", "exportacao"]:
            operation_type = self._operation_type_from_group(group_key)
            preposto_payload = ((servicos.get(group_key) or {}).get("preposto") or {})

            if not self._is_service_enabled_payload(preposto_payload):
                continue

            selected_id = self._extract_preposto_id(preposto_payload)
            if not selected_id:
                continue

            preposto = Preposto.query.filter_by(id=selected_id).first()
            if not preposto:
                continue

            link = ScopePreposto.query.filter_by(
                scope_id=scope.id,
                preposto_id=preposto.id,
                operation_type=operation_type,
            ).first()

            if not link:
                link = ScopePreposto(
                    scope_id=scope.id,
                    preposto_id=preposto.id,
                    operation_type=operation_type,
                )
                db.session.add(link)
                counters["created"] += 1
            else:
                counters["updated"] += 1

            link.enabled = True
            link.valor = self._to_decimal(preposto_payload.get("valor"))
            link.incluso_no_desembaraco_casco = self._to_bool_or_none(
                preposto_payload.get("inclusoNoDesembaracoCasco")
            )
            link.extra_data = preposto_payload

            seen_keys.add((str(preposto.id), operation_type))

        active_links = ScopePreposto.query.filter_by(scope_id=scope.id, enabled=True).all()
        for link in active_links:
            key = (str(link.preposto_id), link.operation_type)
            if key not in seen_keys:
                link.enabled = False
                counters["disabled"] += 1

        return counters

    # ---------------------------------------------------------------------
    # Bulk assignment operations
    # ---------------------------------------------------------------------
    def _bulk_group_config(self, group_by: str) -> dict[str, Any]:
        config = self.BULK_ASSIGNMENT_GROUPS.get(group_by)
        if not config:
            allowed = ", ".join(sorted(self.BULK_ASSIGNMENT_GROUPS))
            raise ValueError(f"groupBy inválido. Valores aceitos: {allowed}.")
        return config

    def _isoformat_z(self, value: datetime | None) -> str | None:
        if not value:
            return None
        return value.isoformat() + "Z"

    def _apply_org_filter_to_scope_query(self, query):
        if self.organization_id:
            return query.filter(Scope.organization_id == self.organization_id)
        return query

    def _validate_bulk_user(self, user_id: str, *, require_active: bool = False) -> User:
        query = User.query.filter(User.id == user_id)
        if self.organization_id:
            query = query.filter(User.organization_id == self.organization_id)
        if require_active:
            query = query.filter(User.ativo.is_(True))

        user = query.first()
        if not user:
            status = "ativo " if require_active else ""
            raise ValueError(f"Usuário {status}não encontrado na organização atual.")
        return user

    def _replace_user_in_list(self, values: Any, from_user_id: str, to_user_id: str) -> list[str]:
        if not isinstance(values, list):
            return values

        replaced: list[str] = []
        for value in values:
            normalized = str(value) if value is not None else value
            if normalized == from_user_id:
                normalized = to_user_id
            if normalized and normalized not in replaced:
                replaced.append(normalized)
        return replaced

    def _set_nested_value(self, payload: dict, path: tuple[str, ...], value: Any) -> None:
        cursor = payload
        for key in path[:-1]:
            if not isinstance(cursor.get(key), dict):
                cursor[key] = {}
            cursor = cursor[key]
        cursor[path[-1]] = value

    def _get_nested_value(self, payload: dict, path: tuple[str, ...]) -> Any:
        cursor: Any = payload
        for key in path:
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(key)
        return cursor

    def _update_scope_draft_for_bulk_assignment(
        self,
        scope: Scope,
        group_by: str,
        from_user_id: str,
        to_user_id: str,
    ) -> None:
        draft = deepcopy(scope.draft or {})

        if group_by == "responsavel_comercial":
            self._set_nested_value(draft, ("sobreEmpresa", "responsavelComercial"), to_user_id)
            if self._get_nested_value(draft, ("sobreEmpresa", "responsavelComercialId")) is not None:
                self._set_nested_value(draft, ("sobreEmpresa", "responsavelComercialId"), to_user_id)
        else:
            for path in self._bulk_group_config(group_by).get("draft_paths", ()):  # type: ignore[union-attr]
                current = self._get_nested_value(draft, path)
                if isinstance(current, list):
                    self._set_nested_value(
                        draft,
                        path,
                        self._replace_user_in_list(current, from_user_id, to_user_id),
                    )

        scope.draft = draft

    def _upsert_active_assignment(self, scope_id, user_id: str, role: str, now: datetime) -> None:
        existing = ScopeAssignment.query.filter_by(
            scope_id=scope_id,
            user_id=user_id,
            role=role,
            active=True,
        ).first()
        if existing:
            return

        db.session.add(
            ScopeAssignment(
                scope_id=scope_id,
                user_id=user_id,
                role=role,
                active=True,
                starts_at=now,
            )
        )

    def _move_active_assignments(
        self,
        scope: Scope,
        roles: tuple[str, ...],
        from_user_id: str,
        to_user_id: str,
        now: datetime,
    ) -> bool:
        source_assignments = ScopeAssignment.query.filter(
            ScopeAssignment.scope_id == scope.id,
            ScopeAssignment.user_id == from_user_id,
            ScopeAssignment.role.in_(roles),
            ScopeAssignment.active.is_(True),
        ).all()

        if not source_assignments:
            return False

        roles_to_move = {assignment.role for assignment in source_assignments}
        for assignment in source_assignments:
            assignment.active = False
            assignment.ends_at = now

        for role in roles_to_move:
            self._upsert_active_assignment(scope.id, to_user_id, role, now)

        return True

    def get_bulk_assignment_summary(self, group_by: str) -> dict[str, Any]:
        config = self._bulk_group_config(group_by)

        if config.get("scope_field") == "responsible_user_id":
            query = (
                db.session.query(
                    User.id.label("user_id"),
                    User.nome.label("user_name"),
                    User.role.label("user_role"),
                    User.setor.label("user_setor"),
                    func.count(Scope.id).label("total_scopes"),
                )
                .join(Scope, Scope.responsible_user_id == User.id)
            )
            if self.organization_id:
                query = query.filter(Scope.organization_id == self.organization_id)
        else:
            roles = config["roles"]
            query = (
                db.session.query(
                    User.id.label("user_id"),
                    User.nome.label("user_name"),
                    User.role.label("user_role"),
                    User.setor.label("user_setor"),
                    func.count(distinct(Scope.id)).label("total_scopes"),
                )
                .join(ScopeAssignment, ScopeAssignment.user_id == User.id)
                .join(Scope, Scope.id == ScopeAssignment.scope_id)
                .filter(
                    ScopeAssignment.active.is_(True),
                    ScopeAssignment.role.in_(roles),
                )
            )
            if self.organization_id:
                query = query.filter(Scope.organization_id == self.organization_id)

        rows = (
            query.group_by(User.id, User.nome, User.role, User.setor)
            .order_by(User.nome.asc())
            .all()
        )

        items = [
            {
                "userId": str(row.user_id),
                "userName": row.user_name,
                "userRole": row.user_role,
                "userSetor": row.user_setor,
                "totalScopes": int(row.total_scopes or 0),
            }
            for row in rows
        ]

        return {
            "groupBy": group_by,
            "totalUsers": len(items),
            "totalScopes": sum(item["totalScopes"] for item in items),
            "items": items,
        }

    def get_bulk_assignment_scopes(self, group_by: str, user_id: str) -> dict[str, Any]:
        config = self._bulk_group_config(group_by)
        self._validate_bulk_user(user_id)

        if config.get("scope_field") == "responsible_user_id":
            query = Scope.query.filter(Scope.responsible_user_id == user_id)
        else:
            scope_ids_subquery = (
                db.session.query(ScopeAssignment.scope_id)
                .filter(
                    ScopeAssignment.user_id == user_id,
                    ScopeAssignment.role.in_(config["roles"]),
                    ScopeAssignment.active.is_(True),
                )
                .distinct()
                .subquery()
            )

            query = Scope.query.join(
                scope_ids_subquery,
                Scope.id == scope_ids_subquery.c.scope_id,
            )

        query = self._apply_org_filter_to_scope_query(query)
        scopes = query.order_by(Scope.updated_at.desc().nullslast(), Scope.created_at.desc()).all()

        items = [
            {
                "id": str(scope.id),
                "status": scope.status,
                "clientName": scope.client.razao_social if scope.client else None,
                "clientCnpj": scope.client.cnpj if scope.client else None,
                "updatedAt": self._isoformat_z(scope.updated_at),
            }
            for scope in scopes
        ]

        return {
            "groupBy": group_by,
            "userId": str(user_id),
            "total": len(items),
            "items": items,
        }

    def bulk_update_assignment(
        self,
        group_by: str,
        from_user_id: str,
        to_user_id: str,
        scope_ids: list[str],
    ) -> dict[str, Any]:
        config = self._bulk_group_config(group_by)

        if not from_user_id or not to_user_id:
            raise ValueError("fromUserId e toUserId são obrigatórios.")
        if str(from_user_id) == str(to_user_id):
            raise ValueError("fromUserId e toUserId devem ser diferentes.")
        if not isinstance(scope_ids, list) or not scope_ids:
            raise ValueError("scopeIds deve ser uma lista não vazia.")

        from_user_id = str(from_user_id)
        to_user_id = str(to_user_id)
        scope_ids = [str(scope_id) for scope_id in scope_ids if scope_id]
        if not scope_ids:
            raise ValueError("scopeIds deve conter identificadores válidos.")

        self._validate_bulk_user(from_user_id)
        self._validate_bulk_user(to_user_id, require_active=True)

        query = Scope.query.filter(Scope.id.in_(scope_ids))
        query = self._apply_org_filter_to_scope_query(query)
        scopes = query.all()

        scopes_by_id = {str(scope.id): scope for scope in scopes}
        ordered_scopes = [scopes_by_id[scope_id] for scope_id in scope_ids if scope_id in scopes_by_id]
        now = datetime.utcnow()
        updated_scope_ids: list[str] = []

        for scope in ordered_scopes:
            changed = False

            if config.get("scope_field") == "responsible_user_id":
                if str(scope.responsible_user_id) != from_user_id:
                    continue
                scope.responsible_user_id = to_user_id
                self._move_active_assignments(
                    scope,
                    config["roles"],
                    from_user_id,
                    to_user_id,
                    now,
                )
                changed = True
            else:
                changed = self._move_active_assignments(
                    scope,
                    config["roles"],
                    from_user_id,
                    to_user_id,
                    now,
                )

            if changed:
                self._update_scope_draft_for_bulk_assignment(scope, group_by, from_user_id, to_user_id)
                updated_scope_ids.append(str(scope.id))

        return {
            "ok": True,
            "impactedScopes": len(updated_scope_ids),
            "updatedScopeIds": updated_scope_ids,
        }

    # ---------------------------------------------------------------------
    # Sync / auditoria
    # ---------------------------------------------------------------------
    def get_sync_missing(self, scope: Scope, draft: dict | None = None) -> dict[str, list[str]]:
        draft = draft or scope.draft or {}
        missing: dict[str, list[str]] = {
            "client": [],
            "assignments": [],
            "services": [],
            "prepostos": [],
        }

        sobre_empresa = draft.get("sobreEmpresa") or {}
        if sobre_empresa.get("cnpj") and sobre_empresa.get("razaoSocial") and not scope.client_id:
            missing["client"].append("client_id")

        for user_id, role in self._extract_assignment_targets(draft):
            exists = ScopeAssignment.query.filter_by(
                scope_id=scope.id,
                user_id=user_id,
                role=role,
                active=True,
            ).first()
            if not exists:
                missing["assignments"].append(f"{role}:{user_id}")

        servicos = draft.get("servicos") or {}

        for operation_type, raw_service_code, _payload in self._iter_enabled_service_payloads(draft):
            code = self._catalog_code(operation_type, raw_service_code)
            catalog = ServiceCatalog.query.filter_by(
                organization_id=scope.organization_id,
                code=code,
            ).first()
            if not catalog:
                missing["services"].append(code)
                continue

            exists = ScopeService.query.filter_by(
                scope_id=scope.id,
                service_catalog_id=catalog.id,
                enabled=True,
            ).first()
            if not exists:
                missing["services"].append(code)

        for group_key in ["importacao", "exportacao"]:
            operation_type = self._operation_type_from_group(group_key)
            preposto_payload = ((servicos.get(group_key) or {}).get("preposto") or {})

            if not self._is_service_enabled_payload(preposto_payload):
                continue

            selected_id = self._extract_preposto_id(preposto_payload)
            if selected_id:
                exists = ScopePreposto.query.filter_by(
                    scope_id=scope.id,
                    preposto_id=selected_id,
                    operation_type=operation_type,
                    enabled=True,
                ).first()
                if not exists:
                    missing["prepostos"].append(f"{operation_type}:{selected_id}")
            elif self._is_manual_preposto_payload(preposto_payload):
                missing["prepostos"].append(f"{operation_type}:MANUAL_SEM_PREPOSTO_ID")

        return {key: value for key, value in missing.items() if value}

    def is_scope_synced(self, scope: Scope, draft: dict | None = None) -> bool:
        return not bool(self.get_sync_missing(scope, draft=draft))

    def sync_scope(self, scope: Scope, dry_run: bool = False) -> ScopeSyncResult:
        normalized_draft = self.normalize_draft(scope.draft or {})
        missing = self.get_sync_missing(scope, draft=normalized_draft)
        already_synced = not bool(missing)

        result = ScopeSyncResult(
            scope_id=str(scope.id),
            already_synced=already_synced,
            changed=False,
            dry_run=dry_run,
            missing=missing,
            created={},
            updated={},
            deactivated={},
        )

        if dry_run or already_synced:
            return result

        self.apply_draft_to_scope(scope, normalized_draft)
        client = self.upsert_client_from_draft(scope, normalized_draft)
        assignment_counters = self.sync_assignments_from_draft(scope, normalized_draft)
        service_counters = self.sync_services_from_draft(scope, normalized_draft)
        preposto_counters = self.sync_prepostos_from_draft(scope, normalized_draft)

        result.changed = True
        result.created = {
            "client": 1 if client else 0,
            "assignments": assignment_counters.get("created", 0),
            "services": service_counters.get("created", 0),
            "prepostos": preposto_counters.get("created", 0),
        }
        result.updated = {
            "assignments": assignment_counters.get("updated", 0),
            "services": service_counters.get("updated", 0),
            "prepostos": preposto_counters.get("updated", 0),
        }
        result.deactivated = {
            "assignments": assignment_counters.get("deactivated", 0),
            "prepostos": preposto_counters.get("disabled", 0),
        }
        return result

    def sync_scopes(self, scopes: list[Scope], dry_run: bool = False) -> list[ScopeSyncResult]:
        return [self.sync_scope(scope, dry_run=dry_run) for scope in scopes]
    
    def list_organization_scope_templates(self):
        query = ScopeTemplate.query
        if self.organization_id:
            query = query.filter(ScopeTemplate.organization_id == self.organization_id)
        return query
