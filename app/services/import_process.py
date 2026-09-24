from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_

from app.cnpj import is_valid_cnpj, normalize_cnpj
from app.nfe_reference import NFE_TRANSPORT_MODE_CODES
from ..extensions import db
from ..integrations.portal_unico import (
    DefaultPortalCredentialResolver,
    PortalUnicoDuimpGateway,
    PortalUnicoIntegrationError,
)
from ..models import Client
from ..models import (
    ClientFiscalProfile,
    ClientImportTaxRule,
    DuimpSnapshot,
    ExternalApiRequestLog,
    ExternalProvider,
    ExternalProviderConnection,
    FiscalEnvironment,
    HttpMethod,
    ImportProcess,
    ImportProcessSource,
    ImportProcessStatus,
    ImportPurpose,
    NfeDraft,
    NfeDraftItem,
    NfeDocumentPlan,
    NfeItemClassification,
    NfePlannedDocument,
    NfePlannedDocumentItem,
    NfeDraftStatus,
    NfeModel,
    NfeCarrier,
    NfeOperationType,
    NfePurpose,
    NfeXmlType,
    NfeXmlVersion,
)

from app.services.nfe_access_key_service import NfeAccessKeyService
from app.services.nfe_number_service import NfeNumberSequenceService
from app.services.duimp_normalizer import DuimpNormalizer
from app.services.fiscal_certificate import (
    CertificateVault,
    DefaultCertificateVault,
    FiscalCertificateError,
)
from app.services.fiscal_certificate_registry import FiscalCertificateRegistry
from app.services.fiscal_reference import FiscalReferenceService
from app.services.import_tax_calculator import ImportTaxCalculator
from app.services.nfe_issuance_state import NfeIdempotency
from app.services.nfe_context import NfeContextResolver
from app.services.nfe_xml_builder import NfeXmlBuilder
from app.services.nfe_xml_signer import (
    NfeXmlSignatureError,
    NfeXmlSigner,
)
from app.services.nfe_xsd_validator import (
    NfeXsdConfigurationError,
    NfeXsdValidationResult,
    NfeXsdValidator,
)
from app.models.nfe_issuance import (
    NfeAttemptOperation,
    NfeAttemptStatus,
    NfeIssuance,
    NfeIssuanceAttempt,
    NfeIssuanceEvent,
)
from app.models.import_process import NfeNumberSequence


@dataclass
class ValidationResult:
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.is_valid, "errors": self.errors, "warnings": self.warnings}


class ImportTaxRuleConflictError(ValueError):
    """Raised when active tax rules can win with the same specificity."""

    def __init__(self, conflicts: list[dict[str, Any]]):
        self.conflicts = conflicts
        names = ", ".join(
            f'"{conflict["name"]}"'
            for conflict in conflicts
            if conflict.get("name")
        )
        detail = f" Regras conflitantes: {names}." if names else ""
        super().__init__(
            "Há regras tributárias ativas com a mesma prioridade e "
            "critérios sobrepostos. Ajuste a prioridade, "
            f"vigência, NCM ou demais filtros antes de continuar.{detail}"
        )


class MockDuimpGateway:
    """Gateway temporário para desenvolvimento sem dependência do Portal Único."""

    def fetch_duimp(
        self,
        *,
        duimp_number: str,
        duimp_payload: dict[str, Any] | None = None,
        enrich_catalog: bool = True,
    ) -> dict[str, Any]:
        if duimp_payload:
            return duimp_payload

        return {
            "numero": duimp_number,
            "versao": "1",
            "dataRegistro": datetime.utcnow().date().isoformat(),
            "localDesembaraco": "PARANAGUA",
            "ufDesembaraco": "PR",
            "dataDesembaraco": datetime.utcnow().date().isoformat(),
            "viaTransporteCodigo": "1",
            "tipoIntermedio": "1",
            "codigoExportador": "EXPORTADOR-EXEMPLO",
            "itens": [
                {
                    "numeroItem": "1",
                    "codigoProduto": "ITEM-1",
                    "descricao": "Produto importado exemplo",
                    "ncm": "85044010",
                    "quantidade": "1.0000",
                    "unidade": "UN",
                    "valorUnitario": "100.0000000000",
                    "valorProduto": "100.00",
                    "numeroAdicao": "1",
                    "sequenciaAdicao": "1",
                    "codigoFabricante": "FABRICANTE-EXEMPLO",
                }
            ],
        }


class ImportNfeService:
    DEFAULT_NFE_ENVIRONMENT = FiscalEnvironment.PRODUCTION.value
    DEFAULT_PROVIDER_ENVIRONMENT = FiscalEnvironment.PRODUCTION.value
    DEFAULT_NFE_SERIES = "1"
    DEFAULT_TABX_CUSTOMS_UNIT_TABLE = "UNIDADE_ADUANEIRA"
    DEFAULT_TABX_COUNTRY_TABLE = "PAIS"

    def __init__(
        self,
        current_user,
        duimp_gateway: Any | None = None,
        credential_resolver: Any | None = None,
        xsd_validator: NfeXsdValidator | None = None,
        certificate_vault: CertificateVault | None = None,
        xml_signer: NfeXmlSigner | None = None,
    ):
        self.current_user = current_user
        self.organization_id = getattr(current_user, "organization_id", None)
        self.user_id = getattr(current_user, "id", None)
        self.duimp_gateway = duimp_gateway
        self.credential_resolver = (
            credential_resolver or DefaultPortalCredentialResolver()
        )
        self.duimp_normalizer = DuimpNormalizer()
        self.tax_calculator = ImportTaxCalculator()
        self.nfe_context_resolver = NfeContextResolver()
        self.xml_builder = NfeXmlBuilder()
        self.xsd_validator = xsd_validator or NfeXsdValidator()
        self.certificate_vault = (
            certificate_vault or DefaultCertificateVault()
        )
        self.certificate_registry = FiscalCertificateRegistry(
            current_user=current_user,
            vault=self.certificate_vault,
        )
        self.xml_signer = xml_signer or NfeXmlSigner()

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------
    def import_process_query_for_current_user(self):
        return ImportProcess.query.filter(
            ImportProcess.organization_id == self._require_organization_id()
        )

    def provider_connection_query_for_current_user(self):
        return ExternalProviderConnection.query.filter(
            ExternalProviderConnection.organization_id
            == self._require_organization_id()
        )

    def nfe_draft_query_for_current_user(self, *, include_removed: bool = False):
        query = NfeDraft.query.filter(
            NfeDraft.organization_id == self._require_organization_id()
        )
        if not include_removed:
            query = query.filter(NfeDraft.deleted_at.is_(None))
        return query

    def snapshot_query_for_current_user(self):
        return DuimpSnapshot.query.filter(
            DuimpSnapshot.organization_id == self._require_organization_id()
        )

    def item_classification_query_for_current_user(self):
        return NfeItemClassification.query.filter(
            NfeItemClassification.organization_id
            == self._require_organization_id()
        )

    def document_plan_query_for_current_user(self):
        return NfeDocumentPlan.query.filter(
            NfeDocumentPlan.organization_id == self._require_organization_id()
        )

    def client_fiscal_profile_query_for_current_user(self):
        return ClientFiscalProfile.query.filter(
            ClientFiscalProfile.organization_id
            == self._require_organization_id()
        )

    def import_tax_rule_query_for_current_user(self):
        return ClientImportTaxRule.query.filter(
            ClientImportTaxRule.organization_id
            == self._require_organization_id()
        )

    def get_nfe_draft_or_404(self, draft_id) -> NfeDraft:
        draft = self.nfe_draft_query_for_current_user().filter(NfeDraft.id == draft_id).first()
        if not draft:
            raise ValueError("Rascunho fiscal não encontrado.")
        return draft
    
    def generate_access_key_for_draft(self, draft_id):
        draft = self.get_nfe_draft_or_404(draft_id)

        if draft.status not in ["ready_for_xml", "xml_generated", "validation_failed"]:
            raise ValueError(
                f"Status atual do rascunho não permite geração da chave de acesso: {draft.status}"
            )

        validation_result = self.validate_nfe_payload(draft.fiscal_payload)
        validation = validation_result.to_dict()

        if not validation["valid"]:
            draft.status = "validation_failed"
            draft.validation_errors = validation["errors"]
            draft.validation_warnings = validation["warnings"]
            draft.updated_at = datetime.now()

            raise ValueError(
                "Rascunho fiscal inválido. Corrija os erros antes de gerar a chave de acesso."
            )

        now = datetime.now(ZoneInfo("America/Sao_Paulo")).replace(microsecond=0)

        if not draft.number:
            sequence_service = NfeNumberSequenceService(current_user=self.current_user)

            draft.number = sequence_service.reserve_next_number(
                client_id=draft.importer_id,
                environment=draft.environment,
                model=draft.model,
                series=draft.series,
            )

        access_key_service = NfeAccessKeyService()

        access_key_data = access_key_service.generate_for_draft(
            draft=draft,
            issue_datetime=now,
            tp_emis="1",
        )

        fiscal_payload = deepcopy(draft.fiscal_payload or {})
        document = fiscal_payload.get("document") or {}

        document.update(
            {
                "access_key": access_key_data["access_key"],
                "state_code": access_key_data["cUF"],
                "aamm": access_key_data["AAMM"],
                "cnf": access_key_data["cNF"],
                "check_digit": access_key_data["cDV"],
                "tp_emis": access_key_data["tpEmis"],
                "issue_datetime": now.isoformat(),
                "number": draft.number,
                "series": draft.series,
                "model": draft.model,
            }
        )

        fiscal_payload["document"] = document

        draft.access_key = access_key_data["access_key"]
        draft.fiscal_payload = fiscal_payload
        draft.status = "ready_for_xml"
        draft.validation_errors = []
        draft.validation_warnings = validation["warnings"]
        draft.updated_at = now

        return {
            "draft": draft,
            "access_key": access_key_data,
            "validation": validation,
        }

    # ------------------------------------------------------------------
    # Client fiscal profile
    # ------------------------------------------------------------------
    def get_client_for_current_user(self, client_id):
        client = (
            Client.query
            .filter(
                Client.id == client_id,
                Client.organization_id == self._require_organization_id(),
            )
            .first()
        )
        if not client:
            raise ValueError("Cliente não encontrado para a organização atual.")
        return client

    def get_importer_fiscal_profile(self, importer_id) -> ClientFiscalProfile:
        profile = (
            self.client_fiscal_profile_query_for_current_user()
            .filter(
                ClientFiscalProfile.client_id == importer_id,
                ClientFiscalProfile.is_default.is_(True),
            )
            .first()
        )
        if not profile:
            raise ValueError(
                "Perfil fiscal do importador não encontrado. "
                "Cadastre CNPJ, inscrição estadual, regime tributário e endereço fiscal antes de gerar a NF-e."
            )
        return profile

    def get_importer_fiscal_profile_or_none(self, importer_id) -> ClientFiscalProfile | None:
        return (
            self.client_fiscal_profile_query_for_current_user()
            .filter(
                ClientFiscalProfile.client_id == importer_id,
                ClientFiscalProfile.is_default.is_(True),
            )
            .first()
        )

    def create_or_update_client_fiscal_profile(self, payload: dict[str, Any]) -> ClientFiscalProfile:
        """Cria ou atualiza o perfil fiscal default de um cliente/importador.

        Espera que o schema da rota já tenha validado os campos obrigatórios.
        Mesmo assim, normaliza CNPJ, IE, CEP, UF e campos vazios.
        """
        client_id = payload.get("client_id")
        if not client_id:
            raise ValueError("client_id é obrigatório.")

        client = self.get_client_for_current_user(client_id)

        payload_cnpj = normalize_cnpj(payload["cnpj"])
        if payload_cnpj != normalize_cnpj(client.cnpj):
            raise ValueError(
                "O CNPJ do perfil fiscal deve ser igual ao CNPJ do cliente."
            )

        profile = self.get_importer_fiscal_profile_or_none(client_id)
        now = datetime.utcnow()

        if not profile:
            profile = ClientFiscalProfile(
                organization_id=self._require_organization_id(),
                client_id=client_id,
                is_default=True,
                created_at=now,
                updated_at=now,
            )
            db.session.add(profile)

        profile.legal_name = str(payload["legal_name"]).strip()
        profile.trade_name = self._empty_to_none(payload.get("trade_name"))
        profile.cnpj = payload_cnpj
        profile.state_registration = self._empty_to_none(self._digits(payload.get("state_registration")))
        profile.tax_regime = str(payload["tax_regime"])

        profile.street = str(payload["street"]).strip()
        profile.number = str(payload["number"]).strip()
        profile.complement = self._empty_to_none(payload.get("complement"))
        profile.district = str(payload["district"]).strip()
        profile.city_code = self._digits(payload["city_code"])
        profile.city_name = str(payload["city_name"]).strip()
        profile.state = str(payload["state"]).strip().upper()
        profile.zip_code = self._digits(payload["zip_code"])

        profile.country_code = self._digits(payload.get("country_code") or "1058")
        profile.country_name = payload.get("country_name") or "Brasil"
        profile.phone = self._empty_to_none(self._digits(payload.get("phone")))
        profile.email = self._empty_to_none(payload.get("email"))
        profile.is_default = bool(payload.get("is_default", True))
        profile.updated_at = now

        self.validate_client_fiscal_profile(profile)
        db.session.flush()
        return profile

    def validate_client_fiscal_profile(self, profile: ClientFiscalProfile) -> None:
        errors = []
        if not is_valid_cnpj(profile.cnpj):
            errors.append(
                "CNPJ do perfil fiscal deve conter 14 caracteres e dígitos verificadores válidos."
            )
        if not profile.legal_name:
            errors.append("Razão social do perfil fiscal é obrigatória.")
        if profile.tax_regime not in {"1", "2", "3"}:
            errors.append("Regime tributário deve ser 1, 2 ou 3.")
        if not profile.street:
            errors.append("Logradouro é obrigatório.")
        if not profile.number:
            errors.append("Número do endereço é obrigatório.")
        if not profile.district:
            errors.append("Bairro é obrigatório.")
        if len(self._digits(profile.city_code)) != 7:
            errors.append("Código IBGE do município deve conter 7 dígitos.")
        if not profile.city_name:
            errors.append("Município é obrigatório.")
        if len(str(profile.state or "")) != 2:
            errors.append("UF deve conter 2 caracteres.")
        if len(self._digits(profile.zip_code)) != 8:
            errors.append("CEP deve conter 8 dígitos.")
        if errors:
            raise ValueError("Perfil fiscal inválido: " + " ".join(errors))

    # ------------------------------------------------------------------
    # Import process
    # ------------------------------------------------------------------
    def create_import_process(self, payload: dict[str, Any]) -> ImportProcess:
        now = datetime.utcnow()
        self.get_client_for_current_user(payload["importer_id"])

        process = ImportProcess(
            organization_id=self._require_organization_id(),
            importer_id=payload["importer_id"],
            reference_code=(
                payload.get("reference_code")
                or f"NFE-{uuid4().hex[:12].upper()}"
            ),
            duimp_number=payload.get("duimp_number"),
            duimp_version=payload.get("duimp_version"),
            source=payload.get("source") or ImportProcessSource.MANUAL.value,
            status=ImportProcessStatus.CREATED.value,
            created_by_user_id=self.user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(process)
        db.session.flush()
        return process

    def update_import_process(self, process: ImportProcess, payload: dict[str, Any]) -> ImportProcess:
        for field in ["reference_code", "duimp_number", "duimp_version", "status", "source"]:
            if field in payload:
                setattr(process, field, payload[field])
        process.updated_at = datetime.utcnow()
        db.session.flush()
        return process

    def _filtered_import_process_query(self, params: dict[str, Any]):
        query = self.import_process_query_for_current_user().join(
            Client,
            Client.id == ImportProcess.importer_id,
        )

        if params.get("created_by_me"):
            query = query.filter(ImportProcess.created_by_user_id == self.user_id)
        if params.get("status"):
            query = query.filter(ImportProcess.status == params["status"])
        if params.get("source"):
            query = query.filter(ImportProcess.source == params["source"])
        if params.get("importer_id"):
            query = query.filter(ImportProcess.importer_id == params["importer_id"])
        if params.get("duimp_number"):
            query = query.filter(ImportProcess.duimp_number == params["duimp_number"])
        if params.get("q"):
            term = f"%{str(params['q']).strip()}%"
            query = query.filter(
                or_(
                    Client.razao_social.ilike(term),
                    Client.nome_resumido.ilike(term),
                    Client.cnpj.ilike(term),
                    ImportProcess.reference_code.ilike(term),
                    ImportProcess.duimp_number.ilike(term),
                )
            )
        return query

    def list_import_processes(self, params: dict[str, Any]) -> dict[str, Any]:
        query = self._filtered_import_process_query(params)
        total = query.count()
        rows = (
            query.order_by(
                ImportProcess.updated_at.desc().nullslast(),
                ImportProcess.created_at.desc(),
            )
            .limit(params["limit"])
            .offset(params["offset"])
            .all()
        )
        return {
            "items": [
                self.build_import_process_list_summary(row)
                for row in rows
            ],
            "total": total,
            **params,
        }

    def get_import_process_dashboard_summary(self) -> dict[str, Any]:
        rows = (
            self.import_process_query_for_current_user()
            .with_entities(
                ImportProcess.status,
                func.count(ImportProcess.id),
            )
            .group_by(ImportProcess.status)
            .all()
        )
        by_status = {
            status: 0
            for status in ImportProcessStatus.values()
        }
        for status, count in rows:
            by_status[self._enum_value(status)] = int(count)

        terminal_statuses = {
            ImportProcessStatus.AUTHORIZED.value,
            ImportProcessStatus.CANCELLED.value,
        }
        ready_for_emission_statuses = {
            ImportProcessStatus.DRAFT_READY.value,
            ImportProcessStatus.XML_GENERATED.value,
            ImportProcessStatus.XML_VALIDATED.value,
            ImportProcessStatus.XML_SIGNED.value,
            ImportProcessStatus.TRANSMISSION_PENDING.value,
        }
        attention_required_statuses = {
            ImportProcessStatus.DUIMP_FETCH_FAILED.value,
            ImportProcessStatus.DRAFT_VALIDATION_FAILED.value,
            ImportProcessStatus.XML_VALIDATION_FAILED.value,
            ImportProcessStatus.REJECTED.value,
            ImportProcessStatus.FAILED.value,
        }

        return {
            "total": sum(by_status.values()),
            "in_progress": sum(
                count
                for status, count in by_status.items()
                if status not in terminal_statuses
            ),
            "ready_for_emission": sum(
                by_status[status]
                for status in ready_for_emission_statuses
            ),
            "attention_required": sum(
                by_status[status]
                for status in attention_required_statuses
            ),
            "completed": by_status[ImportProcessStatus.AUTHORIZED.value],
            "by_status": by_status,
        }

    def list_import_process_client_groups(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Agrupa clientes no banco sem recalcular o workflow por processo."""
        terminal_statuses = [
            ImportProcessStatus.XML_VALIDATED.value,
            ImportProcessStatus.XML_SIGNED.value,
            ImportProcessStatus.AUTHORIZED.value,
            ImportProcessStatus.CANCELLED.value,
        ]
        last_activity = func.max(
            func.coalesce(
                ImportProcess.updated_at,
                ImportProcess.created_at,
            )
        ).label("last_updated_at")
        pending_count = func.sum(
            case(
                (
                    ImportProcess.status.in_(terminal_statuses),
                    0,
                ),
                else_=1,
            )
        ).label("pending_count")

        grouped_query = (
            self._filtered_import_process_query(params)
            .with_entities(
                Client.id.label("client_id"),
                Client.nome_resumido.label("short_name"),
                Client.razao_social.label("legal_name"),
                Client.cnpj.label("cnpj"),
                func.count(ImportProcess.id).label("process_count"),
                pending_count,
                last_activity,
            )
            .group_by(
                Client.id,
                Client.nome_resumido,
                Client.razao_social,
                Client.cnpj,
            )
        )
        total = grouped_query.order_by(None).count()
        rows = (
            grouped_query
            .order_by(last_activity.desc())
            .limit(params["limit"])
            .offset(params["offset"])
            .all()
        )

        return {
            "items": [
                {
                    "client_id": str(row.client_id),
                    "name": row.short_name or row.legal_name,
                    "legal_name": row.legal_name,
                    "cnpj": row.cnpj,
                    "process_count": int(row.process_count or 0),
                    "pending_count": int(row.pending_count or 0),
                    "last_updated_at": self._iso(row.last_updated_at),
                }
                for row in rows
            ],
            "total": total,
            "limit": params["limit"],
            "offset": params["offset"],
            "q": params.get("q"),
            "created_by_me": params.get("created_by_me", False),
        }

    def build_import_process_list_summary(
        self,
        process: ImportProcess,
    ) -> dict[str, Any]:
        summary = self.build_import_process_summary(process)
        workflow = self.get_nfe_workflow_state(
            process,
            {
                "import_purpose": None,
                "environment": FiscalEnvironment.HOMOLOGATION.value,
                "series": "1",
            },
        )
        next_action = workflow["next_action"]
        process_status = self._enum_value(process.status)
        terminal_statuses = {
            ImportProcessStatus.XML_VALIDATED.value,
            ImportProcessStatus.XML_SIGNED.value,
            ImportProcessStatus.AUTHORIZED.value,
            ImportProcessStatus.CANCELLED.value,
        }
        drafts_count = (
            NfeDraft.query
            .filter(
                NfeDraft.import_process_id == process.id,
                NfeDraft.deleted_at.is_(None),
            )
            .count()
        )
        active_plan = (
            self.document_plan_query_for_current_user()
            .filter(
                NfeDocumentPlan.import_process_id == process.id,
                NfeDocumentPlan.status == "planned",
            )
            .order_by(NfeDocumentPlan.version_number.desc())
            .first()
        )
        responsible = process.created_by
        importer = process.importer
        summary.update(
            {
                "importer": {
                    "id": str(importer.id),
                    "name": importer.nome_resumido or importer.razao_social,
                    "legal_name": importer.razao_social,
                    "cnpj": importer.cnpj,
                },
                "next_action": next_action,
                "pending": (
                    next_action != "completed"
                    and process_status not in terminal_statuses
                ),
                "planned_documents_count": (
                    len(active_plan.documents) if active_plan else drafts_count
                ),
                "last_responsible": (
                    {
                        "id": str(responsible.id),
                        "name": responsible.nome,
                        "is_current_user": responsible.id == self.user_id,
                    }
                    if responsible
                    else None
                ),
            }
        )
        return summary

    def build_import_process_summary(self, process: ImportProcess) -> dict[str, Any]:
        latest_draft = (
            NfeDraft.query.filter(
                NfeDraft.import_process_id == process.id,
                NfeDraft.deleted_at.is_(None),
            )
            .order_by(NfeDraft.updated_at.desc().nullslast(), NfeDraft.created_at.desc())
            .first()
        )
        snapshots_count = DuimpSnapshot.query.filter(DuimpSnapshot.import_process_id == process.id).count()
        items_count = 0
        if latest_draft:
            items_count = NfeDraftItem.query.filter(NfeDraftItem.nfe_draft_id == latest_draft.id).count()

        profile_exists = self.get_importer_fiscal_profile_or_none(process.importer_id) is not None

        return {
            "id": str(process.id),
            "organization_id": str(process.organization_id),
            "importer_id": str(process.importer_id),
            "reference_code": process.reference_code,
            "duimp_number": process.duimp_number,
            "duimp_version": process.duimp_version,
            "status": process.status,
            "source": process.source,
            "created_by_user_id": (
                str(process.created_by_user_id)
                if process.created_by_user_id
                else None
            ),
            "created_by_me": process.created_by_user_id == self.user_id,
            "has_fiscal_profile": profile_exists,
            "snapshots_count": snapshots_count,
            "latest_draft_id": str(latest_draft.id) if latest_draft else None,
            "latest_draft_status": latest_draft.status if latest_draft else None,
            "items_count": items_count,
            "created_at": self._iso(process.created_at),
            "updated_at": self._iso(process.updated_at),
        }

    @staticmethod
    def _workflow_navigation(next_action: str) -> dict[str, Any]:
        steps = [
            ("client", "Cliente"),
            ("duimp", "DUIMP"),
            ("context", "Contexto fiscal"),
            ("purposes", "Finalidades"),
            ("planning", "Plano de notas"),
            ("drafts", "Rascunho"),
            ("xml", "XML"),
            ("review", "Conferência"),
        ]
        action_step = {
            "configure_fiscal_profile": "client",
            "configure_tax_rule": "client",
            "configure_number_sequence": "client",
            "configure_provider_connection": "duimp",
            "fetch_duimp": "duimp",
            "resolve_context": "context",
            "classify_items": "purposes",
            "create_document_plan": "planning",
            "review_document_plan": "planning",
            "create_child_drafts": "drafts",
            "create_draft": "drafts",
            "correct_child_drafts": "drafts",
            "correct_draft": "drafts",
            "generate_child_xmls": "xml",
            "validate_child_xmls": "xml",
            "generate_access_key": "xml",
            "generate_xml": "xml",
            "validate_xml": "xml",
            "completed": "review",
        }
        current_step = action_step.get(next_action, "duimp")
        current_index = next(
            index
            for index, (key, _) in enumerate(steps)
            if key == current_step
        )
        return {
            "current_step": current_step,
            "furthest_available_step": current_step,
            "steps": [
                {
                    "key": key,
                    "label": label,
                    "status": (
                        "completed"
                        if index < current_index
                        else "current"
                        if index == current_index
                        else "blocked"
                    ),
                    "can_view": index <= current_index,
                }
                for index, (key, label) in enumerate(steps)
            ],
        }

    def get_nfe_workflow_state(
        self,
        process: ImportProcess,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        latest_snapshot = (
            self.snapshot_query_for_current_user()
            .filter(DuimpSnapshot.import_process_id == process.id)
            .order_by(DuimpSnapshot.created_at.desc())
            .first()
        )
        latest_draft = (
            self.nfe_draft_query_for_current_user()
            .filter(NfeDraft.import_process_id == process.id)
            .order_by(
                NfeDraft.updated_at.desc().nullslast(),
                NfeDraft.created_at.desc(),
            )
            .first()
        )
        latest_plan = None
        if latest_snapshot:
            latest_plan = (
                self.document_plan_query_for_current_user()
                .filter(
                    NfeDocumentPlan.import_process_id == process.id,
                    NfeDocumentPlan.duimp_snapshot_id == latest_snapshot.id,
                    NfeDocumentPlan.status == "planned",
                )
                .order_by(NfeDocumentPlan.version_number.desc())
                .first()
            )

        import_purpose = params.get("import_purpose")
        environment = self.DEFAULT_NFE_ENVIRONMENT
        series = params.get("series") or self.DEFAULT_NFE_SERIES
        if latest_draft:
            document = (latest_draft.fiscal_payload or {}).get("document") or {}
            import_purpose = import_purpose or document.get("import_purpose")
            environment = self._enum_value(latest_draft.environment) or environment
            series = latest_draft.series or series

        fiscal_profile = self.get_importer_fiscal_profile_or_none(
            process.importer_id
        )
        context = None
        if latest_snapshot:
            context = self.get_nfe_context(
                process,
                {
                    "duimp_snapshot_id": latest_snapshot.id,
                },
            )

        classifications = None
        latest_classification = None
        if latest_snapshot and fiscal_profile:
            classifications = self.get_item_classification_state(
                process,
                latest_snapshot.id,
            )
            latest_classification = (
                self.item_classification_query_for_current_user()
                .filter(
                    NfeItemClassification.import_process_id == process.id,
                    NfeItemClassification.duimp_snapshot_id == latest_snapshot.id,
                )
                .order_by(NfeItemClassification.updated_at.desc())
                .first()
            )

        comparable_series = {series, series.zfill(3)}
        sequence = (
            NfeNumberSequence.query.filter(
                NfeNumberSequence.organization_id == self._require_organization_id(),
                NfeNumberSequence.client_id == process.importer_id,
                NfeNumberSequence.environment == environment,
                NfeNumberSequence.model == NfeModel.NFE.value,
                NfeNumberSequence.series.in_(comparable_series),
            )
            .order_by(NfeNumberSequence.updated_at.desc())
            .first()
        )

        draft_detail = self.get_nfe_draft_detail(latest_draft) if latest_draft else None
        latest_xml = (
            draft_detail["xml_versions"][0]
            if draft_detail and draft_detail["xml_versions"]
            else None
        )
        tax_rule_diagnostics = self.import_tax_rule_diagnostics(
            process.importer_id
        )
        active_tax_rule_count = tax_rule_diagnostics["summary"]["active"]
        active_tax_rule = active_tax_rule_count > 0
        try:
            provider_connection = self._find_provider_connection(
                process=process,
                provider=ExternalProvider.PORTAL_UNICO.value,
                environment=self.DEFAULT_PROVIDER_ENVIRONMENT,
            )
            has_provider_connection = bool(provider_connection.credentials_ref)
        except PortalUnicoIntegrationError:
            has_provider_connection = False
        classification_newer_than_draft = bool(
            latest_draft
            and latest_classification
            and latest_classification.updated_at
            and latest_draft.created_at
            and latest_classification.updated_at > latest_draft.created_at
        )
        classification_newer_than_plan = bool(
            latest_plan
            and latest_classification
            and latest_classification.updated_at
            and latest_plan.created_at
            and latest_classification.updated_at > latest_plan.created_at
        )
        classification_required = bool(
            classifications
            and (
                latest_draft is None
                or classifications.get("has_classifications")
            )
            and not classifications.get("ready_for_draft")
        )
        child_documents = list(latest_plan.documents) if latest_plan else []
        is_multi_document_plan = len(child_documents) > 1
        child_drafts = [
            self._latest_active_document_draft(document)
            for document in child_documents
        ]
        missing_child_drafts = bool(is_multi_document_plan) and any(
            draft is None for draft in child_drafts
        )
        child_correction_required = bool(is_multi_document_plan) and any(
            draft is not None and bool(draft.validation_errors)
            for draft in child_drafts
        )
        child_xmls = []
        for draft in child_drafts:
            if draft is None:
                child_xmls.append(None)
                continue
            child_xmls.append(
                NfeXmlVersion.query.filter(
                    NfeXmlVersion.nfe_draft_id == draft.id,
                    NfeXmlVersion.xml_type == NfeXmlType.UNSIGNED.value,
                )
                .order_by(NfeXmlVersion.version_number.desc())
                .first()
            )
        missing_child_xmls = bool(is_multi_document_plan) and any(
            xml is None for xml in child_xmls
        )
        invalid_child_xmls = bool(is_multi_document_plan) and any(
            xml is not None and xml.xsd_valid is not True
            for xml in child_xmls
        )

        if fiscal_profile is None:
            next_action = "configure_fiscal_profile"
        elif sequence is None and latest_snapshot is None:
            next_action = "configure_number_sequence"
        elif not has_provider_connection and latest_snapshot is None:
            next_action = "configure_provider_connection"
        elif latest_snapshot is None:
            next_action = "fetch_duimp"
        elif context and not context.get("ready_for_draft"):
            next_action = "resolve_context"
        elif classification_required:
            next_action = "classify_items"
        elif (
            classifications
            and classifications.get("ready_for_draft")
            and (
                latest_plan is None
                or classification_newer_than_plan
            )
            and (
                latest_draft is None
                or classification_newer_than_draft
            )
        ):
            next_action = "create_document_plan"
        elif is_multi_document_plan and missing_child_drafts:
            next_action = "create_child_drafts"
        elif is_multi_document_plan and child_correction_required:
            next_action = "correct_child_drafts"
        elif is_multi_document_plan and sequence is None:
            next_action = "configure_number_sequence"
        elif is_multi_document_plan and missing_child_xmls:
            next_action = "generate_child_xmls"
        elif is_multi_document_plan and invalid_child_xmls:
            next_action = "validate_child_xmls"
        elif is_multi_document_plan:
            next_action = "completed"
        elif latest_draft is None:
            next_action = "create_draft"
        elif classification_newer_than_draft:
            next_action = "create_draft"
        elif sequence is None:
            next_action = "configure_number_sequence"
        elif latest_draft.validation_errors:
            next_action = "correct_draft"
        elif not latest_draft.access_key:
            next_action = "generate_access_key"
        elif latest_xml is None:
            next_action = "generate_xml"
        elif (
            latest_draft.updated_at
            and latest_xml.generated_at
            and latest_draft.updated_at > latest_xml.generated_at
        ):
            next_action = "generate_xml"
        elif latest_xml.xsd_valid is not True:
            next_action = "validate_xml"
        else:
            next_action = "completed"

        navigation = self._workflow_navigation(next_action)

        return {
            "process": self.build_import_process_summary(process),
            "latest_snapshot": (
                {
                    "id": str(latest_snapshot.id),
                    "duimp_number": latest_snapshot.duimp_number,
                    "duimp_version": latest_snapshot.duimp_version,
                    "source_provider": self._enum_value(latest_snapshot.source_provider),
                    "fetched_at": self._iso(latest_snapshot.fetched_at),
                    "created_at": self._iso(latest_snapshot.created_at),
                }
                if latest_snapshot
                else None
            ),
            "context": context,
            "item_classification": classifications,
            "document_plan": (
                self._serialize_document_plan(latest_plan)
                if latest_plan
                else None
            ),
            "latest_draft": draft_detail,
            "prerequisites": {
                "has_fiscal_profile": fiscal_profile is not None,
                "has_active_tax_rule": active_tax_rule,
                "active_tax_rule_count": active_tax_rule_count,
                "tax_rule_conflict_count": tax_rule_diagnostics["summary"][
                    "conflict_count"
                ],
                "has_number_sequence": sequence is not None,
                "has_provider_connection": has_provider_connection,
                "has_item_classification": bool(
                    classifications
                    and classifications.get("has_classifications")
                ),
                "item_classification_ready": bool(
                    classifications
                    and classifications.get("ready_for_draft")
                ),
                "has_document_plan": latest_plan is not None,
                "planned_documents_count": (
                    len(latest_plan.documents) if latest_plan else 0
                ),
                "import_purpose": import_purpose,
                "environment": environment,
                "series": series,
            },
            "next_action": next_action,
            **navigation,
        }

    # ------------------------------------------------------------------
    # Provider connections
    # ------------------------------------------------------------------
    def create_provider_connection(self, payload: dict[str, Any]) -> ExternalProviderConnection:
        """Cria ou atualiza a conexão do mesmo contexto, provider e ambiente."""
        now = datetime.utcnow()
        organization_id = self._require_organization_id()
        importer_id = payload.get("importer_id")
        provider = payload["provider"]
        environment = payload["environment"]
        status = payload.get("status") or "active"
        credentials_ref = self._empty_to_none(payload.get("credentials_ref"))

        if importer_id:
            self.get_client_for_current_user(importer_id)
        if (
            provider == ExternalProvider.PORTAL_UNICO.value
            and status == "active"
            and not credentials_ref
        ):
            raise ValueError(
                "credentials_ref é obrigatório para uma conexão ativa do Portal Único."
            )

        query = self.provider_connection_query_for_current_user().filter(
            ExternalProviderConnection.provider == provider,
            ExternalProviderConnection.environment == environment,
        )
        if importer_id:
            query = query.filter(
                ExternalProviderConnection.importer_id == importer_id
            )
        else:
            query = query.filter(ExternalProviderConnection.importer_id.is_(None))

        connection = query.order_by(
            ExternalProviderConnection.updated_at.desc()
        ).first()
        if connection is None:
            connection = ExternalProviderConnection(
                organization_id=organization_id,
                importer_id=importer_id,
                provider=provider,
                environment=environment,
                created_at=now,
            )
            db.session.add(connection)

        connection.auth_type = payload["auth_type"]
        connection.status = status
        connection.config_json = payload.get("config_json")
        connection.credentials_ref = credentials_ref
        connection.last_error = None
        connection.updated_at = now
        db.session.flush()
        return connection

    def list_provider_connections(self, params: dict[str, Any]) -> dict[str, Any]:
        query = self.provider_connection_query_for_current_user()
        for field in ["importer_id", "provider", "environment", "status"]:
            if params.get(field):
                query = query.filter(getattr(ExternalProviderConnection, field) == params[field])

        total = query.count()
        rows = (
            query.order_by(ExternalProviderConnection.updated_at.desc())
            .limit(params["limit"])
            .offset(params["offset"])
            .all()
        )
        return {"items": rows, "total": total, "limit": params["limit"], "offset": params["offset"]}

    # ------------------------------------------------------------------
    # Regras fiscais de importação
    # ------------------------------------------------------------------
    def list_import_tax_rules(self, client_id) -> list[ClientImportTaxRule]:
        self.get_client_for_current_user(client_id)
        return (
            self.import_tax_rule_query_for_current_user()
            .filter(ClientImportTaxRule.client_id == client_id)
            .order_by(
                ClientImportTaxRule.active.desc(),
                ClientImportTaxRule.priority.desc(),
                ClientImportTaxRule.name.asc(),
            )
            .all()
        )

    def create_import_tax_rule(
        self,
        client_id,
        payload: dict[str, Any],
    ) -> ClientImportTaxRule:
        self.get_client_for_current_user(client_id)
        now = datetime.utcnow()
        rule = ClientImportTaxRule(
            organization_id=self._require_organization_id(),
            client_id=client_id,
            created_by_user_id=self.user_id,
            created_at=now,
            updated_at=now,
            **payload,
        )
        self._validate_import_tax_rule(rule)
        conflicts = self._find_import_tax_rule_conflicts(rule)
        if conflicts:
            raise ImportTaxRuleConflictError(conflicts)
        db.session.add(rule)
        db.session.flush()
        return rule

    def update_import_tax_rule(
        self,
        rule: ClientImportTaxRule,
        payload: dict[str, Any],
    ) -> ClientImportTaxRule:
        revision_fields = {
            "issuer_state",
            "import_purpose",
            "import_modality",
            "tax_regime",
            "ncm_pattern",
            "ncm_scope_type",
            "ncm_patterns",
            "priority",
            "configuration_json",
            "additional_cost_defaults",
            "transport_defaults",
            "payment_defaults",
            "effective_from",
            "effective_until",
        }
        for field in (
            "name",
            "issuer_state",
            "import_purpose",
            "import_modality",
            "tax_regime",
            "ncm_pattern",
            "ncm_scope_type",
            "ncm_patterns",
            "priority",
            "configuration_json",
            "additional_cost_defaults",
            "transport_defaults",
            "payment_defaults",
            "active",
            "effective_from",
            "effective_until",
        ):
            if field in payload:
                setattr(rule, field, payload[field])
        if revision_fields.intersection(payload):
            rule.revision = (rule.revision or 1) + 1
        rule.updated_at = datetime.utcnow()
        self._validate_import_tax_rule(rule)
        conflicts = self._find_import_tax_rule_conflicts(rule)
        if conflicts:
            raise ImportTaxRuleConflictError(conflicts)
        db.session.flush()
        return rule

    def get_import_tax_rule(self, client_id, rule_id) -> ClientImportTaxRule:
        self.get_client_for_current_user(client_id)
        rule = (
            self.import_tax_rule_query_for_current_user()
            .filter(
                ClientImportTaxRule.id == rule_id,
                ClientImportTaxRule.client_id == client_id,
            )
            .first()
        )
        if rule is None:
            raise ValueError("Regra fiscal de importação não encontrada.")
        return rule

    def deactivate_import_tax_rule(self, rule: ClientImportTaxRule) -> None:
        rule.active = False
        rule.updated_at = datetime.utcnow()
        db.session.flush()

    def import_tax_rule_diagnostics(self, client_id) -> dict[str, Any]:
        """Return all rules and any ambiguous active-rule pairs.

        New ambiguous pairs are rejected during create/update. This diagnosis
        also exposes duplicates created before this validation existed so the
        operator can correct the exact records without losing the workflow.
        """
        rules = self.list_import_tax_rules(client_id)
        conflicts_by_rule: dict[str, list[dict[str, Any]]] = {
            str(rule.id): [] for rule in rules
        }
        pairs: list[dict[str, Any]] = []
        for index, rule in enumerate(rules):
            if not rule.active:
                continue
            for other in rules[index + 1:]:
                if not other.active or not self._tax_rules_are_ambiguous(rule, other):
                    continue
                rule_summary = self._tax_rule_conflict_summary(rule)
                other_summary = self._tax_rule_conflict_summary(other)
                conflicts_by_rule[str(rule.id)].append(other_summary)
                conflicts_by_rule[str(other.id)].append(rule_summary)
                pairs.append({"rules": [rule_summary, other_summary]})
        return {
            "rules": rules,
            "conflicts_by_rule": conflicts_by_rule,
            "conflicts": pairs,
            "summary": {
                "total": len(rules),
                "active": sum(1 for rule in rules if rule.active),
                "inactive": sum(1 for rule in rules if not rule.active),
                "conflict_count": len(pairs),
            },
        }

    def match_import_tax_rule(
        self,
        *,
        client_id,
        issuer_state: str,
        tax_regime: str,
        import_purpose: str,
        import_modality: str | None,
        ncm: str | None = None,
        ncms: list[str] | None = None,
        reference_date: date | None = None,
        rule_id=None,
    ) -> ClientImportTaxRule | None:
        resolved_ncm = self._single_ncm(ncm=ncm, ncms=ncms)
        result = self.resolve_import_tax_rule(
            client_id=client_id,
            issuer_state=issuer_state,
            tax_regime=tax_regime,
            import_purpose=import_purpose,
            import_modality=import_modality,
            ncm=resolved_ncm,
            reference_date=reference_date,
            rule_id=rule_id,
        )
        return result["rule"]

    def resolve_import_tax_rule(
        self,
        *,
        client_id,
        issuer_state: str,
        tax_regime: str,
        import_purpose: str,
        import_modality: str | None,
        ncm: str,
        reference_date: date | None,
        rule_id=None,
    ) -> dict[str, Any]:
        self.get_client_for_current_user(client_id)
        normalized_ncm = self._digits(ncm)
        if len(normalized_ncm) != 8:
            raise ValueError("O NCM deve conter exatamente 8 dígitos.")

        query = self.import_tax_rule_query_for_current_user().filter(
            ClientImportTaxRule.client_id == client_id,
            ClientImportTaxRule.active.is_(True),
        )
        if rule_id:
            query = query.filter(ClientImportTaxRule.id == rule_id)

        candidates = []
        for rule in query.all():
            reasons = self._tax_rule_mismatch_reasons(
                rule,
                issuer_state=issuer_state,
                tax_regime=tax_regime,
                import_purpose=import_purpose,
                import_modality=import_modality,
                ncm=normalized_ncm,
                reference_date=reference_date,
            )
            score = (
                self._tax_rule_score(rule, normalized_ncm)
                if not reasons
                else None
            )
            candidates.append(
                {
                    "rule": rule,
                    "mismatch_reasons": reasons,
                    "score": score,
                    "matched_ncm_pattern": self._matching_ncm_pattern(
                        rule,
                        normalized_ncm,
                    ),
                }
            )
        matches = [candidate for candidate in candidates if not candidate["mismatch_reasons"]]

        matches.sort(key=lambda candidate: candidate["score"], reverse=True)
        if rule_id and not matches:
            raise ValueError(
                "A regra fiscal informada não é aplicável ao cliente, UF, "
                "finalidade, modalidade, NCM ou período da DUIMP."
            )
        if (
            len(matches) > 1
            and not rule_id
            and matches[0]["score"] == matches[1]["score"]
        ):
            top_score = matches[0]["score"]
            raise ImportTaxRuleConflictError(
                [
                    self._tax_rule_conflict_summary(candidate["rule"])
                    for candidate in matches
                    if candidate["score"] == top_score
                ]
            )
        selected = matches[0] if matches else None
        return {
            "rule": selected["rule"] if selected else None,
            "selection": {
                "ncm": normalized_ncm,
                "matched_ncm_pattern": (
                    selected["matched_ncm_pattern"] if selected else None
                ),
                "score": list(selected["score"]) if selected else None,
                "explicit_rule": bool(rule_id),
            },
            "candidates": [
                {
                    "rule": self._tax_rule_to_dict(candidate["rule"]),
                    "mismatch_reasons": candidate["mismatch_reasons"],
                    "score": (
                        list(candidate["score"])
                        if candidate["score"] is not None
                        else None
                    ),
                    "matched_ncm_pattern": candidate["matched_ncm_pattern"],
                    "selected": bool(
                        selected
                        and candidate["rule"].id == selected["rule"].id
                    ),
                }
                for candidate in sorted(
                    candidates,
                    key=lambda candidate: (
                        candidate["score"] is not None,
                        candidate["score"] or (-1, -1, False, False),
                        candidate["rule"].name,
                    ),
                    reverse=True,
                )
            ],
        }

    def simulate_import_tax_rule(
        self,
        client_id,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = self.resolve_import_tax_rule(
            client_id=client_id,
            issuer_state=payload["issuer_state"],
            tax_regime=payload["tax_regime"],
            import_purpose=payload["import_purpose"],
            import_modality=payload.get("import_modality"),
            ncm=payload["ncm"],
            reference_date=payload.get("reference_date"),
            rule_id=payload.get("tax_rule_id"),
        )
        rule = result.pop("rule")
        return {
            "status": "matched" if rule else "no_match",
            "selected_rule": self._tax_rule_to_dict(rule) if rule else None,
            **result,
        }

    @classmethod
    def _single_ncm(
        cls,
        *,
        ncm: str | None,
        ncms: list[str] | None,
    ) -> str:
        values = [cls._digits(value) for value in (ncms or []) if value]
        if ncm:
            values.insert(0, cls._digits(ncm))
        unique = list(dict.fromkeys(values))
        if len(unique) != 1:
            raise ValueError(
                "A regra tributária deve ser resolvida individualmente para "
                "cada item da DUIMP."
            )
        return unique[0]

    def _find_import_tax_rule_conflicts(
        self,
        rule: ClientImportTaxRule,
    ) -> list[dict[str, Any]]:
        if not rule.active:
            return []
        query = self.import_tax_rule_query_for_current_user().filter(
            ClientImportTaxRule.client_id == rule.client_id,
            ClientImportTaxRule.active.is_(True),
        )
        if rule.id:
            query = query.filter(ClientImportTaxRule.id != rule.id)
        return [
            self._tax_rule_conflict_summary(candidate)
            for candidate in query.all()
            if self._tax_rules_are_ambiguous(rule, candidate)
        ]

    @classmethod
    def _tax_rules_are_ambiguous(
        cls,
        first: ClientImportTaxRule,
        second: ClientImportTaxRule,
    ) -> bool:
        if first.priority != second.priority:
            return False
        if bool(first.import_modality) != bool(second.import_modality):
            return False
        if bool(first.tax_regime) != bool(second.tax_regime):
            return False
        if first.issuer_state != second.issuer_state:
            return False
        if first.import_purpose != second.import_purpose:
            return False
        if not cls._optional_scope_overlaps(
            first.import_modality,
            second.import_modality,
        ):
            return False
        if not cls._optional_scope_overlaps(first.tax_regime, second.tax_regime):
            return False
        if not cls._ncm_scopes_tie(first, second):
            return False
        if (
            first.effective_until
            and second.effective_from
            and first.effective_until < second.effective_from
        ):
            return False
        if (
            second.effective_until
            and first.effective_from
            and second.effective_until < first.effective_from
        ):
            return False
        return True

    @staticmethod
    def _optional_scope_overlaps(first: str | None, second: str | None) -> bool:
        return not first or not second or first == second

    @classmethod
    def _tax_rule_score(
        cls,
        rule: ClientImportTaxRule,
        ncm: str | None = None,
    ) -> tuple[int, int, bool, bool]:
        if ncm:
            specificity = len(cls._matching_ncm_pattern(rule, ncm) or "")
        else:
            specificity = max(
                (len(pattern) for pattern in cls._rule_ncm_patterns(rule)),
                default=0,
            )
        return (
            specificity,
            rule.priority,
            bool(rule.import_modality),
            bool(rule.tax_regime),
        )

    @staticmethod
    def _tax_rule_conflict_summary(rule: ClientImportTaxRule) -> dict[str, Any]:
        return {
            "id": str(rule.id) if rule.id else None,
            "name": rule.name,
            "issuer_state": rule.issuer_state,
            "import_purpose": rule.import_purpose,
            "import_modality": rule.import_modality,
            "tax_regime": rule.tax_regime,
            "ncm_pattern": rule.ncm_pattern,
            "priority": rule.priority,
            "effective_from": (
                rule.effective_from.isoformat() if rule.effective_from else None
            ),
            "effective_until": (
                rule.effective_until.isoformat() if rule.effective_until else None
            ),
        }

    def _tax_rule_mismatch_reasons(
        self,
        rule: ClientImportTaxRule,
        *,
        issuer_state: str,
        tax_regime: str,
        import_purpose: str,
        import_modality: str | None,
        ncm: str,
        reference_date: date | None,
    ) -> list[str]:
        reasons: list[str] = []
        if rule.issuer_state != issuer_state:
            reasons.append("issuer_state")
        if rule.import_purpose != import_purpose:
            reasons.append("import_purpose")
        if rule.tax_regime and rule.tax_regime != tax_regime:
            reasons.append("tax_regime")
        if rule.import_modality and rule.import_modality != import_modality:
            reasons.append("import_modality")
        if reference_date:
            if rule.effective_from and reference_date < rule.effective_from:
                reasons.append("effective_from")
            if rule.effective_until and reference_date > rule.effective_until:
                reasons.append("effective_until")
        if self._matching_ncm_pattern(rule, ncm) is None:
            reasons.append("ncm_pattern")
        return reasons

    @classmethod
    def _rule_ncm_scope_type(cls, rule: ClientImportTaxRule) -> str:
        explicit = str(rule.ncm_scope_type or "").strip()
        if explicit in {"all", "prefix", "exact"}:
            return explicit
        legacy = cls._digits(rule.ncm_pattern)
        if not legacy:
            return "all"
        return "exact" if len(legacy) == 8 else "prefix"

    @classmethod
    def _rule_ncm_patterns(cls, rule: ClientImportTaxRule) -> list[str]:
        configured = rule.ncm_patterns
        if isinstance(configured, list):
            patterns = [cls._digits(value) for value in configured]
            patterns = [value for value in patterns if value]
            if patterns:
                return sorted(set(patterns))
        legacy = cls._digits(rule.ncm_pattern)
        return [legacy] if legacy else []

    @classmethod
    def _matching_ncm_pattern(
        cls,
        rule: ClientImportTaxRule,
        ncm: str,
    ) -> str | None:
        normalized_ncm = cls._digits(ncm)
        patterns = cls._rule_ncm_patterns(rule)
        if not patterns:
            return ""
        matches = [
            pattern
            for pattern in patterns
            if normalized_ncm.startswith(pattern)
        ]
        return max(matches, key=len) if matches else None

    @classmethod
    def _ncm_scopes_tie(
        cls,
        first: ClientImportTaxRule,
        second: ClientImportTaxRule,
    ) -> bool:
        first_patterns = cls._rule_ncm_patterns(first) or [""]
        second_patterns = cls._rule_ncm_patterns(second) or [""]
        return any(
            first_pattern == second_pattern
            for first_pattern in first_patterns
            for second_pattern in second_patterns
        )

    @staticmethod
    def _validate_import_tax_rule(rule: ClientImportTaxRule) -> None:
        if (
            rule.effective_from
            and rule.effective_until
            and rule.effective_until < rule.effective_from
        ):
            raise ValueError(
                "effective_until não pode ser anterior a effective_from."
            )

    # ------------------------------------------------------------------
    # DUIMP snapshot
    # ------------------------------------------------------------------
    def create_manual_duimp_snapshot(self, process: ImportProcess, payload: dict[str, Any]) -> DuimpSnapshot:
        normalized = payload.get("normalized_payload") or self.normalize_duimp_payload(payload["raw_payload"])
        snapshot = self._create_duimp_snapshot(
            process=process,
            duimp_number=payload["duimp_number"],
            duimp_version=payload.get("duimp_version"),
            raw_payload=payload["raw_payload"],
            normalized_payload=normalized,
            source_provider=payload.get("source_provider") or ExternalProvider.PORTAL_UNICO.value,
        )
        process.duimp_number = payload["duimp_number"]
        process.duimp_version = payload.get("duimp_version")
        process.status = ImportProcessStatus.DUIMP_FETCHED.value
        process.updated_at = datetime.utcnow()
        db.session.flush()
        return snapshot

    def fetch_duimp_for_process(
        self, process: ImportProcess, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if not process.duimp_number and not payload.get("duimp_payload"):
            raise ValueError(
                "Processo não possui duimp_number e nenhum duimp_payload foi enviado."
            )

        provider = payload.get("source_provider") or ExternalProvider.PORTAL_UNICO.value
        gateway = self._duimp_gateway_for(process=process, payload=payload)
        started_at = datetime.utcnow()
        process.status = ImportProcessStatus.DUIMP_FETCHING.value
        process.updated_at = started_at
        db.session.flush()

        manual_payload = payload.get("duimp_payload")
        duimp_number = process.duimp_number
        if not duimp_number and manual_payload:
            duimp_number = manual_payload.get("numero") or manual_payload.get("number")

        try:
            raw_duimp = gateway.fetch_duimp(
                duimp_number=duimp_number,
                duimp_payload=manual_payload,
                enrich_catalog=payload.get("enrich_catalog", True),
            )
            finished_at = datetime.utcnow()
            self._log_external_request(
                process=process,
                provider=provider,
                endpoint_name="duimp.fetch",
                method=HttpMethod.GET.value,
                request_payload={
                    "duimp_number": duimp_number,
                    "enrich_catalog": payload.get("enrich_catalog", True),
                },
                response_payload=raw_duimp,
                success=True,
                status_code=200,
                started_at=started_at,
                finished_at=finished_at,
            )
        except Exception as exc:
            finished_at = datetime.utcnow()
            process.status = ImportProcessStatus.DUIMP_FETCH_FAILED.value
            process.updated_at = finished_at
            self._log_external_request(
                process=process,
                provider=provider,
                endpoint_name="duimp.fetch",
                method=HttpMethod.GET.value,
                request_payload={"duimp_number": duimp_number},
                response_payload=None,
                success=False,
                status_code=getattr(exc, "status_code", None),
                error_code=getattr(exc, "error_code", None)
                or exc.__class__.__name__,
                error_message=str(exc),
                started_at=started_at,
                finished_at=finished_at,
            )
            raise

        normalized = self.normalize_duimp_payload(raw_duimp)
        snapshot = self._create_duimp_snapshot(
            process=process,
            duimp_number=normalized["number"],
            duimp_version=normalized.get("version"),
            raw_payload=raw_duimp,
            normalized_payload=normalized,
            source_provider=provider,
        )
        process.duimp_number = normalized["number"]
        process.duimp_version = normalized.get("version")
        process.status = ImportProcessStatus.DUIMP_FETCHED.value
        process.updated_at = datetime.utcnow()
        db.session.flush()
        return {"snapshot": snapshot, "normalized": normalized}

    def _duimp_gateway_for(
        self, *, process: ImportProcess, payload: dict[str, Any]
    ) -> Any:
        if self.duimp_gateway is not None:
            return self.duimp_gateway
        if payload.get("duimp_payload") is not None:
            return MockDuimpGateway()

        provider = payload.get("source_provider") or ExternalProvider.PORTAL_UNICO.value
        if provider != ExternalProvider.PORTAL_UNICO.value:
            raise ValueError(
                f"Não existe gateway configurado para o provider {provider}."
            )

        environment = payload.get("provider_environment") or payload.get("environment")
        if environment not in FiscalEnvironment.values():
            raise ValueError("Ambiente do provider é obrigatório para consultar a DUIMP.")

        connection = self._find_provider_connection(
            process=process,
            provider=provider,
            environment=environment,
        )
        config = connection.config_json or {}
        role_type = config.get("role_type") or "IMPEXP"
        credentials = self.credential_resolver.resolve(
            connection.credentials_ref,
            role_type=role_type,
        )
        return PortalUnicoDuimpGateway(
            credentials=credentials,
            environment=environment,
            base_url=config.get("base_url"),
            timeout_seconds=float(config.get("timeout_seconds") or 30),
        )

    def _find_provider_connection(
        self,
        *,
        process: ImportProcess,
        provider: str,
        environment: str,
    ) -> ExternalProviderConnection:
        base_query = self.provider_connection_query_for_current_user().filter(
            ExternalProviderConnection.provider == provider,
            ExternalProviderConnection.environment == environment,
            ExternalProviderConnection.status == "active",
        )
        connection = base_query.filter(
            ExternalProviderConnection.importer_id == process.importer_id
        ).first()
        if connection is None:
            connection = base_query.filter(
                ExternalProviderConnection.importer_id.is_(None)
            ).first()
        if connection is None:
            raise PortalUnicoIntegrationError(
                "Conexão ativa com o Portal Único não configurada para o ambiente "
                f"{environment}. Cadastre uma conexão específica do cliente ou global "
                "da organização em /external-provider-connections. Para consultar uma "
                "DUIMP real, use provider_environment=production; o ambiente da NF-e "
                "pode permanecer em homologation."
            )
        if not connection.credentials_ref:
            raise PortalUnicoIntegrationError(
                "A conexão com o Portal Único não possui credentials_ref."
            )
        return connection

    # ------------------------------------------------------------------
    # Contexto automatizado para NF-e
    # ------------------------------------------------------------------
    def get_nfe_context(
        self,
        process: ImportProcess,
        payload: dict[str, Any],
        *,
        persist: bool = False,
    ) -> dict[str, Any]:
        snapshot = self._snapshot_for_process(
            process,
            payload.get("duimp_snapshot_id"),
        )
        normalized = deepcopy(
            snapshot.normalized_payload
            or self.normalize_duimp_payload(snapshot.raw_payload)
        )
        previous_snapshot_id = str(snapshot.id)
        preserved_overrides = self._operator_overrides_from_normalized(normalized)
        refresh_state = {
            "requested": bool(payload.get("refresh_external")),
            "snapshot_changed": False,
            "previous_snapshot_id": previous_snapshot_id,
            "snapshot_id": previous_snapshot_id,
        }

        external: dict[str, Any] = {"errors": []}
        connection_config: dict[str, Any] = {}
        environment = payload.get("provider_environment")
        if payload.get("refresh_external"):
            environment = self.DEFAULT_PROVIDER_ENVIRONMENT
            payload = dict(payload)
            payload["provider_environment"] = environment
        if environment:
            connection = self._find_provider_connection(
                process=process,
                provider=ExternalProvider.PORTAL_UNICO.value,
                environment=environment,
            )
            connection_config = dict(connection.config_json or {})

        if payload.get("refresh_external"):
            if not environment:
                raise ValueError(
                    "provider_environment é obrigatório quando refresh_external=true."
                )
            gateway = self._duimp_gateway_for(process=process, payload=payload)
            refreshed_payload = self._read_external_context(
                process=process,
                endpoint_name="duimp.current_version.get",
                request_payload={"duimp_number": process.duimp_number},
                errors=external["errors"],
                callback=lambda: gateway.fetch_duimp(
                    duimp_number=normalized.get("number") or snapshot.duimp_number,
                    enrich_catalog=True,
                ),
            )
            if isinstance(refreshed_payload, dict):
                refreshed_normalized = self.normalize_duimp_payload(
                    refreshed_payload
                )
                refreshed_checksum = self._checksum(refreshed_payload)
                if refreshed_checksum != snapshot.checksum:
                    snapshot = self._create_duimp_snapshot(
                        process=process,
                        duimp_number=refreshed_normalized["number"],
                        duimp_version=refreshed_normalized.get("version"),
                        raw_payload=refreshed_payload,
                        normalized_payload=refreshed_normalized,
                        source_provider=ExternalProvider.PORTAL_UNICO.value,
                    )
                    refresh_state["snapshot_changed"] = True
                normalized = refreshed_normalized
                process.duimp_number = refreshed_normalized["number"]
                process.duimp_version = refreshed_normalized.get("version")
                process.status = ImportProcessStatus.DUIMP_FETCHED.value
                process.updated_at = datetime.utcnow()
                refresh_state["snapshot_id"] = str(snapshot.id)

            customs_unit_code = normalized.get("clearance_location_code")
            cached_unit = FiscalReferenceService.find_customs_unit(
                customs_unit_code
            )
            if cached_unit:
                external["cached_customs_unit"] = {
                    "code": cached_unit.code,
                    "description": cached_unit.description,
                    "state": cached_unit.state,
                    "municipality_code": cached_unit.municipality_code,
                }

            supplier = normalized.get("foreign_supplier") or {}
            origin = normalized.get("country_of_origin") or {}
            cached_country = FiscalReferenceService.find_country(
                iso_alpha_2=(
                    supplier.get("country_iso_alpha_2")
                    or origin.get("iso_alpha_2")
                ),
                bacen_code=supplier.get("country_code"),
                name=supplier.get("country_name"),
                active_on=(
                    self._date_value(normalized.get("registration_date"))
                    or date.today()
                ),
            )
            if cached_country:
                external["cached_country"] = {
                    "code": cached_country.bacen_code,
                    "name": cached_country.name,
                    "iso_alpha_2": cached_country.iso_alpha_2,
                    "iso_alpha_3": cached_country.iso_alpha_3,
                }

            cargo_identifier = self._cargo_identifier(normalized)
            if cargo_identifier:
                external["cargo_knowledge"] = self._read_external_context(
                    process=process,
                    endpoint_name="cct.cargo_knowledge.get",
                    request_payload={"numeroConhecimento": cargo_identifier},
                    errors=external["errors"],
                    callback=lambda: gateway.fetch_cargo_knowledge(
                        knowledge_number=cargo_identifier
                    ),
                )

            external["icms_declaration"] = self._read_external_context(
                process=process,
                endpoint_name="pcce.icms.get",
                request_payload={"duimp_number": normalized.get("number")},
                errors=external["errors"],
                callback=lambda: gateway.fetch_icms_declaration(
                    duimp_number=normalized.get("number")
                ),
            )

            customs_unit_code = normalized.get("clearance_location_code")
            customs_table = connection_config.get(
                "tabx_customs_unit_table",
                self.DEFAULT_TABX_CUSTOMS_UNIT_TABLE,
            )
            if customs_table and customs_unit_code:
                code_field = connection_config.get(
                    "tabx_customs_unit_code_field", "CODIGO"
                )
                external["customs_unit"] = self._read_external_context(
                    process=process,
                    endpoint_name="tabx.customs_unit.get",
                    request_payload={
                        "table": customs_table,
                        "code": customs_unit_code,
                    },
                    errors=external["errors"],
                    callback=lambda: gateway.fetch_comex_table(
                        table_name=customs_table,
                        filters=[
                            {
                                "nomeTabela": customs_table,
                                "nome": code_field,
                                "valores": [str(customs_unit_code)],
                            }
                        ],
                    ),
                )
                if external.get("customs_unit"):
                    cached_unit = FiscalReferenceService.cache_customs_unit_from_tabx(
                        code=str(customs_unit_code),
                        payload=external["customs_unit"],
                        code_field=code_field,
                        description_field=connection_config.get(
                            "tabx_customs_unit_description_field", "NOME"
                        ),
                        state_field=connection_config.get(
                            "tabx_customs_unit_state_field", "UF"
                        ),
                        municipality_field=connection_config.get(
                            "tabx_customs_unit_municipality_field",
                            "CODIGO_MUNICIPIO",
                        ),
                    )
                    if cached_unit:
                        external["cached_customs_unit"] = {
                            "code": cached_unit.code,
                            "description": cached_unit.description,
                            "state": cached_unit.state,
                            "municipality_code": cached_unit.municipality_code,
                        }

            country_iso = (
                (normalized.get("foreign_supplier") or {}).get(
                    "country_iso_alpha_2"
                )
                or (normalized.get("country_of_origin") or {}).get("iso_alpha_2")
            )
            country_table = connection_config.get(
                "tabx_country_table",
                self.DEFAULT_TABX_COUNTRY_TABLE,
            )
            if country_table and country_iso:
                iso_field = connection_config.get(
                    "tabx_country_iso_field", "SIGLA_ISO2"
                )
                external["country"] = self._read_external_context(
                    process=process,
                    endpoint_name="tabx.country.get",
                    request_payload={"table": country_table, "iso": country_iso},
                    errors=external["errors"],
                    callback=lambda: gateway.fetch_comex_table(
                        table_name=country_table,
                        filters=[
                            {
                                "nomeTabela": country_table,
                                "nome": iso_field,
                                "valores": [str(country_iso)],
                            }
                        ],
                    ),
                )
                if external.get("country"):
                    cached_country = FiscalReferenceService.cache_country_from_tabx(
                        iso_alpha_2=str(country_iso),
                        payload=external["country"],
                        code_field=connection_config.get(
                            "tabx_country_code_field", "CODIGO"
                        ),
                        name_field=connection_config.get(
                            "tabx_country_name_field", "NOME"
                        ),
                        iso_field=iso_field,
                    )
                    if cached_country:
                        external["cached_country"] = {
                            "code": cached_country.bacen_code,
                            "name": cached_country.name,
                            "iso_alpha_2": cached_country.iso_alpha_2,
                            "iso_alpha_3": cached_country.iso_alpha_3,
                        }

        customs_unit_code = normalized.get("clearance_location_code")
        if not external.get("cached_customs_unit"):
            cached_unit = FiscalReferenceService.find_customs_unit(
                customs_unit_code
            )
            if cached_unit:
                external["cached_customs_unit"] = {
                    "code": cached_unit.code,
                    "description": cached_unit.description,
                    "state": cached_unit.state,
                    "municipality_code": cached_unit.municipality_code,
                }

        if not external.get("cached_country"):
            supplier = normalized.get("foreign_supplier") or {}
            origin = normalized.get("country_of_origin") or {}
            cached_country = FiscalReferenceService.find_country(
                iso_alpha_2=(
                    supplier.get("country_iso_alpha_2")
                    or origin.get("iso_alpha_2")
                ),
                bacen_code=supplier.get("country_code"),
                name=supplier.get("country_name"),
                active_on=(
                    self._date_value(normalized.get("registration_date"))
                    or date.today()
                ),
            )
            if cached_country:
                external["cached_country"] = {
                    "code": cached_country.bacen_code,
                    "name": cached_country.name,
                    "iso_alpha_2": cached_country.iso_alpha_2,
                    "iso_alpha_3": cached_country.iso_alpha_3,
                }

        effective_overrides = self._merge_defaults(
            preserved_overrides,
            payload.get("overrides"),
        )
        context = self.nfe_context_resolver.resolve(
            normalized=normalized,
            external=external,
            connection_config=connection_config,
            overrides=effective_overrides,
        )
        fiscal_profile = self.get_importer_fiscal_profile_or_none(process.importer_id)
        rule = None
        resolved_rules: list[ClientImportTaxRule] = []
        has_missing_item_rule = False
        import_purpose = payload.get("import_purpose")
        if fiscal_profile and import_purpose:
            rules_by_id = {}
            for item in context["normalized"].get("items", []):
                item_rule = self.match_import_tax_rule(
                    client_id=process.importer_id,
                    issuer_state=fiscal_profile.state,
                    tax_regime=fiscal_profile.tax_regime,
                    import_purpose=import_purpose,
                    import_modality=context["normalized"].get(
                        "import_modality"
                    ),
                    ncm=self._digits(item.get("ncm")),
                    reference_date=self._date_value(
                        context["normalized"].get("registration_date")
                    ),
                )
                if item_rule is None:
                    has_missing_item_rule = True
                    continue
                rules_by_id[item_rule.id] = item_rule
            resolved_rules = list(rules_by_id.values())
            rule = resolved_rules[0] if len(resolved_rules) == 1 else None

        missing = list(context["missing_fields"])
        if fiscal_profile is None:
            missing.append("client.fiscal_profile")
        if import_purpose and (has_missing_item_rule or not resolved_rules):
            missing.append("tax_configuration")
        context.update(
            {
                "process_id": str(process.id),
                "snapshot_id": str(snapshot.id),
                "refresh": refresh_state,
                "tax_rule": self._tax_rule_to_dict(rule) if rule else None,
                "tax_rules": [
                    self._tax_rule_to_dict(item_rule)
                    for item_rule in resolved_rules
                ],
                "missing_fields": missing,
                "ready_for_draft": not missing,
            }
        )

        if persist:
            snapshot.normalized_payload = context["normalized"]
            db.session.flush()
        return context

    @staticmethod
    def _operator_overrides_from_normalized(
        normalized: dict[str, Any],
    ) -> dict[str, Any]:
        sources = normalized.get("automation_field_sources") or {}
        overrides: dict[str, Any] = {}
        for field in NfeContextResolver.ALLOWED_OVERRIDES:
            if sources.get(field) == "operator_override" and normalized.get(field) not in (None, ""):
                overrides[field] = normalized[field]

        supplier = normalized.get("foreign_supplier") or {}
        supplier_overrides = {
            field: supplier.get(field)
            for field in ("name", "country_code", "country_name", "country_iso_alpha_2")
            if sources.get(f"foreign_supplier.{field}") == "operator_override"
            and supplier.get(field) not in (None, "")
        }
        if supplier_overrides:
            overrides["foreign_supplier"] = supplier_overrides
        return overrides

    def _snapshot_for_process(
        self,
        process: ImportProcess,
        snapshot_id=None,
    ) -> DuimpSnapshot:
        query = self.snapshot_query_for_current_user().filter(
            DuimpSnapshot.import_process_id == process.id
        )
        if snapshot_id:
            try:
                snapshot_uuid = (
                    snapshot_id
                    if isinstance(snapshot_id, UUID)
                    else UUID(str(snapshot_id))
                )
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError("Identificador do snapshot da DUIMP inválido.") from exc
            query = query.filter(DuimpSnapshot.id == snapshot_uuid)
        snapshot = query.order_by(DuimpSnapshot.created_at.desc()).first()
        if snapshot is None:
            raise ValueError("Snapshot da DUIMP não encontrado para este processo.")
        return snapshot

    def _read_external_context(
        self,
        *,
        process: ImportProcess,
        endpoint_name: str,
        request_payload: dict[str, Any],
        errors: list[dict[str, Any]],
        callback,
    ) -> Any:
        started_at = datetime.utcnow()
        try:
            response = callback()
            self._log_external_request(
                process=process,
                provider=ExternalProvider.PORTAL_UNICO.value,
                endpoint_name=endpoint_name,
                method=HttpMethod.GET.value,
                request_payload=request_payload,
                response_payload=response,
                success=True,
                status_code=200,
                started_at=started_at,
                finished_at=datetime.utcnow(),
            )
            return response
        except Exception as exc:
            error = {
                "source": endpoint_name,
                "code": getattr(exc, "error_code", None)
                or exc.__class__.__name__,
                "message": str(exc),
            }
            errors.append(error)
            self._log_external_request(
                process=process,
                provider=ExternalProvider.PORTAL_UNICO.value,
                endpoint_name=endpoint_name,
                method=HttpMethod.GET.value,
                request_payload=request_payload,
                response_payload=None,
                success=False,
                status_code=getattr(exc, "status_code", None),
                error_code=error["code"],
                error_message=error["message"],
                started_at=started_at,
                finished_at=datetime.utcnow(),
            )
            return None

    @staticmethod
    def _cargo_identifier(normalized: dict[str, Any]) -> str | None:
        raw = normalized.get("raw") or {}
        general = raw.get("dadosGerais") or {}
        documents = (general.get("documentos") or {}).get(
            "documentosInstrucao"
        ) or []
        # Documento 30 é o conhecimento aéreo informado na própria DUIMP.
        # Ele deve ser preferido à RUC para a consulta numeroConhecimento do CCT.
        for document in documents:
            document_type = str((document.get("tipo") or {}).get("codigo") or "")
            if document_type != "30":
                continue
            for keyword in document.get("palavrasChave") or []:
                value = keyword.get("valor")
                if value not in (None, ""):
                    return str(value).strip()
        cargo = general.get("carga") or {}
        value = cargo.get("identificacao")
        return str(value).strip() if value not in (None, "") else None

    # ------------------------------------------------------------------
    # Classificação fiscal por item
    # ------------------------------------------------------------------
    def get_item_classification_state(
        self,
        process: ImportProcess,
        snapshot_id=None,
    ) -> dict[str, Any]:
        snapshot = self._snapshot_for_process(process, snapshot_id)
        normalized = (
            snapshot.normalized_payload
            or self.normalize_duimp_payload(snapshot.raw_payload)
        )
        rows = (
            self.item_classification_query_for_current_user()
            .filter(
                NfeItemClassification.import_process_id == process.id,
                NfeItemClassification.duimp_snapshot_id == snapshot.id,
            )
            .all()
        )
        by_number = {
            str(row.duimp_item_number): row
            for row in rows
        }
        profile = self.get_importer_fiscal_profile_or_none(
            process.importer_id
        )
        active_rules = (
            self.import_tax_rule_query_for_current_user()
            .filter(
                ClientImportTaxRule.client_id == process.importer_id,
                ClientImportTaxRule.active.is_(True),
            )
            .all()
        )
        registration_date = self._date_value(
            normalized.get("registration_date")
        )
        items = []
        purpose_counts: dict[str, int] = {}
        latest_updated_at = None

        for source in normalized.get("items") or []:
            number = str(source.get("number") or "")
            row = by_number.get(number)
            purpose = row.import_purpose if row else None
            rule = row.tax_rule if row else None
            rule_active = bool(rule and rule.active)
            applied_revision = (
                (row.tax_rule_snapshot or {}).get("revision")
                if row
                else None
            )
            current_revision = (rule.revision or 1) if rule else None
            if row and row.updated_at and (
                latest_updated_at is None
                or row.updated_at > latest_updated_at
            ):
                latest_updated_at = row.updated_at

            if purpose:
                purpose_counts[purpose] = purpose_counts.get(purpose, 0) + 1
            if row is None:
                status = "unclassified"
            elif not rule:
                status = "missing_tax_rule"
            elif not rule_active:
                status = "inactive_tax_rule"
            elif applied_revision and applied_revision != current_revision:
                status = "stale_tax_rule"
            elif not row.cfop:
                status = "missing_cfop"
            else:
                status = "classified"

            rule_cfop = (
                str((rule.configuration_json or {}).get("cfop") or "")
                if rule
                else ""
            )
            rule_candidates = []
            if (
                purpose
                and status != "classified"
                and profile is not None
            ):
                item_ncm = self._digits(source.get("ncm"))
                for candidate in active_rules:
                    if candidate.import_purpose != purpose:
                        continue
                    reasons = self._tax_rule_mismatch_reasons(
                        candidate,
                        issuer_state=profile.state,
                        tax_regime=profile.tax_regime,
                        import_purpose=purpose,
                        import_modality=normalized.get("import_modality"),
                        ncm=item_ncm,
                        reference_date=registration_date,
                    )
                    rule_candidates.append(
                        {
                            "id": str(candidate.id),
                            "name": candidate.name,
                            "mismatch_reasons": reasons,
                            "issuer_state": candidate.issuer_state,
                            "tax_regime": candidate.tax_regime,
                            "import_modality": candidate.import_modality,
                            "ncm_pattern": candidate.ncm_pattern,
                            "ncm_scope_type": self._rule_ncm_scope_type(
                                candidate
                            ),
                            "ncm_patterns": self._rule_ncm_patterns(candidate),
                            "effective_from": (
                                candidate.effective_from.isoformat()
                                if candidate.effective_from
                                else None
                            ),
                            "effective_until": (
                                candidate.effective_until.isoformat()
                                if candidate.effective_until
                                else None
                            ),
                            "cfop": str(
                                (candidate.configuration_json or {}).get(
                                    "cfop"
                                )
                                or self._resolve_cfop(purpose)
                            ),
                        }
                    )
                rule_candidates.sort(
                    key=lambda candidate: (
                        len(candidate["mismatch_reasons"]),
                        candidate["name"],
                    )
                )
            items.append(
                {
                    "duimp_item_number": number,
                    "product_code": source.get("product_code"),
                    "description": source.get("description"),
                    "ncm": source.get("ncm"),
                    "exporter_code": (
                        source.get("exporter_code")
                        or normalized.get("exporter_code")
                    ),
                    "import_purpose": purpose,
                    "cfop": row.cfop if row else None,
                    "cfop_source": (
                        "tax_rule"
                        if row and rule_cfop == row.cfop
                        else "purpose_default"
                        if row and row.cfop
                        else None
                    ),
                    "tax_rule": (
                        {
                            "id": str(rule.id),
                            "name": rule.name,
                            "active": rule.active,
                            "revision": current_revision,
                            "applied_revision": applied_revision,
                        }
                        if rule
                        else None
                    ),
                    "status": status,
                    "rule_candidates": rule_candidates[:3],
                    "classified_by": (
                        {
                            "id": str(row.classified_by.id),
                            "name": row.classified_by.nome,
                        }
                        if row and row.classified_by
                        else None
                    ),
                    "updated_at": self._iso(row.updated_at) if row else None,
                }
            )

        classified_count = sum(
            1 for item in items if item["status"] == "classified"
        )
        return {
            "process_id": str(process.id),
            "snapshot_id": str(snapshot.id),
            "items": items,
            "total_items": len(items),
            "classified_count": classified_count,
            "pending_count": len(items) - classified_count,
            "purpose_counts": purpose_counts,
            "registration_date": (
                registration_date.isoformat()
                if registration_date
                else None
            ),
            "has_classifications": bool(rows),
            "ready_for_draft": bool(items)
            and classified_count == len(items),
            "latest_updated_at": self._iso(latest_updated_at),
        }

    def save_item_classifications(
        self,
        process: ImportProcess,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = self._snapshot_for_process(
            process,
            payload.get("duimp_snapshot_id"),
        )
        normalized = (
            snapshot.normalized_payload
            or self.normalize_duimp_payload(snapshot.raw_payload)
        )
        source_items = {
            str(item.get("number") or ""): item
            for item in normalized.get("items") or []
        }
        profile = self.get_importer_fiscal_profile(process.importer_id)
        now = datetime.utcnow()

        for requested in payload["items"]:
            item_number = str(requested["duimp_item_number"])
            source = source_items.get(item_number)
            if source is None:
                raise ValueError(
                    f"Item {item_number} não pertence ao snapshot informado."
                )
            purpose = requested["import_purpose"]
            resolution = self.resolve_import_tax_rule(
                client_id=process.importer_id,
                issuer_state=profile.state,
                tax_regime=profile.tax_regime,
                import_purpose=purpose,
                import_modality=normalized.get("import_modality"),
                ncm=self._digits(source.get("ncm")),
                reference_date=self._date_value(
                    normalized.get("registration_date")
                ),
                rule_id=requested.get("tax_rule_id"),
            )
            rule = resolution["rule"]
            configuration = dict(rule.configuration_json or {}) if rule else {}
            configured_cfop = self._digits(configuration.get("cfop"))
            cfop = (
                configured_cfop
                if len(configured_cfop) == 4
                else self._resolve_cfop(purpose)
                if rule
                else None
            )

            row = (
                self.item_classification_query_for_current_user()
                .filter(
                    NfeItemClassification.duimp_snapshot_id == snapshot.id,
                    NfeItemClassification.duimp_item_number == item_number,
                )
                .first()
            )
            if row is None:
                row = NfeItemClassification(
                    organization_id=self._require_organization_id(),
                    import_process_id=process.id,
                    duimp_snapshot_id=snapshot.id,
                    duimp_item_number=item_number,
                    created_at=now,
                )
                db.session.add(row)
            row.import_purpose = purpose
            row.tax_rule_id = rule.id if rule else None
            row.tax_rule_snapshot = (
                self._tax_rule_snapshot(
                    rule,
                    ncm=self._digits(source.get("ncm")),
                    selection=resolution["selection"],
                )
                if rule
                else None
            )
            row.cfop = cfop
            row.source = "manual"
            row.classified_by_user_id = self.user_id
            row.updated_at = now

        process.updated_at = now
        db.session.flush()
        return self.get_item_classification_state(process, snapshot.id)

    def _item_classification_map(
        self,
        process: ImportProcess,
        snapshot: DuimpSnapshot,
    ) -> dict[str, NfeItemClassification]:
        rows = (
            self.item_classification_query_for_current_user()
            .filter(
                NfeItemClassification.import_process_id == process.id,
                NfeItemClassification.duimp_snapshot_id == snapshot.id,
            )
            .all()
        )
        return {
            str(row.duimp_item_number): row
            for row in rows
        }

    # ------------------------------------------------------------------
    # Planejamento documental (Master gerencial e NF-e filhas)
    # ------------------------------------------------------------------
    def get_document_plan_state(
        self,
        process: ImportProcess,
        snapshot_id=None,
    ) -> dict[str, Any]:
        snapshot = self._snapshot_for_process(process, snapshot_id)
        plan = (
            self.document_plan_query_for_current_user()
            .filter(
                NfeDocumentPlan.import_process_id == process.id,
                NfeDocumentPlan.duimp_snapshot_id == snapshot.id,
                NfeDocumentPlan.status == "planned",
            )
            .order_by(NfeDocumentPlan.version_number.desc())
            .first()
        )
        return {
            "process_id": str(process.id),
            "snapshot_id": str(snapshot.id),
            "plan": self._serialize_document_plan(plan) if plan else None,
        }

    def create_document_plan(
        self,
        process: ImportProcess,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._json_compatible(payload)
        snapshot = self._snapshot_for_process(
            process,
            payload.get("duimp_snapshot_id"),
        )
        normalized = (
            snapshot.normalized_payload
            or self.normalize_duimp_payload(snapshot.raw_payload)
        )
        classification_state = self.get_item_classification_state(
            process,
            snapshot.id,
        )
        if not classification_state["ready_for_draft"]:
            raise ValueError(
                "Classifique a finalidade, a regra tributária e o CFOP de "
                "todos os itens antes de gerar o planejamento documental."
            )
        classifications = self._item_classification_map(process, snapshot)
        source_items = list(normalized.get("items") or [])
        if not source_items:
            raise ValueError("A DUIMP não possui itens para planejar.")

        default_costs: dict[str, Any] = {}
        for classification in classifications.values():
            rule = classification.tax_rule
            if rule and rule.additional_cost_defaults:
                default_costs = self._merge_defaults(
                    default_costs,
                    rule.additional_cost_defaults,
                )
        default_costs = self._merge_defaults(
            default_costs,
            normalized.get("automation_additional_costs"),
        )
        default_costs.update(payload.get("additional_costs") or {})
        shared_costs = self.resolve_additional_costs(
            duimp=normalized,
            additional_costs=default_costs,
        )
        shared_costs = {
            name: self._money_text(self._decimal(shared_costs.get(name)))
            for name in ("afrmm", "siscomex_fee", "thc", "other")
        }

        weights = [
            self._decimal(item.get("customs_value") or item.get("product_value"))
            for item in source_items
        ]
        if any(weight < 0 for weight in weights):
            raise ValueError("O valor aduaneiro dos itens não pode ser negativo.")
        if sum(weights, Decimal("0")) <= 0:
            raise ValueError(
                "O valor aduaneiro positivo é obrigatório para ratear os custos."
            )
        allocations = {
            name: self._allocate_shared_cost_to_items(
                self._decimal(value),
                weights,
            )
            for name, value in shared_costs.items()
        }

        now = datetime.utcnow()
        previous_plans = (
            self.document_plan_query_for_current_user()
            .filter(
                NfeDocumentPlan.import_process_id == process.id,
                NfeDocumentPlan.status == "planned",
            )
            .all()
        )
        for previous in previous_plans:
            previous.status = "superseded"
            previous.updated_at = now
        latest_version = (
            db.session.query(func.max(NfeDocumentPlan.version_number))
            .filter(NfeDocumentPlan.duimp_snapshot_id == snapshot.id)
            .scalar()
            or 0
        )
        plan = NfeDocumentPlan(
            organization_id=self._require_organization_id(),
            import_process_id=process.id,
            duimp_snapshot_id=snapshot.id,
            version_number=latest_version + 1,
            status="planned",
            allocation_basis="customs_value",
            shared_costs=shared_costs,
            totals={},
            reconciliation={},
            created_by_user_id=self.user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(plan)
        db.session.flush()

        groups: dict[str, dict[str, Any]] = {}
        for index, source in enumerate(source_items):
            item_number = str(source.get("number") or "")
            classification = classifications[item_number]
            exporter_key, exporter_code, supplier = self._document_exporter(
                source,
                normalized,
            )
            group = groups.setdefault(
                exporter_key,
                {
                    "exporter_code": exporter_code,
                    "foreign_supplier": supplier,
                    "items": [],
                },
            )
            group["items"].append(
                {
                    "source": source,
                    "classification": classification,
                    "customs_value": weights[index],
                    "allocations": {
                        name: values[index]
                        for name, values in allocations.items()
                    },
                }
            )

        document_rows = []
        for ordinal, (exporter_key, group) in enumerate(
            sorted(groups.items(), key=lambda entry: entry[0]),
            start=1,
        ):
            purposes = sorted(
                {
                    item["classification"].import_purpose
                    for item in group["items"]
                }
            )
            mixed = len(purposes) > 1
            group_customs_value = sum(
                (item["customs_value"] for item in group["items"]),
                Decimal("0"),
            )
            group_costs = {
                name: self._money_text(
                    sum(
                        (item["allocations"][name] for item in group["items"]),
                        Decimal("0"),
                    )
                )
                for name in shared_costs
            }
            group_cost_total = sum(
                (self._decimal(value) for value in group_costs.values()),
                Decimal("0"),
            )
            document = NfePlannedDocument(
                organization_id=self._require_organization_id(),
                document_plan_id=plan.id,
                ordinal=ordinal,
                exporter_key=exporter_key,
                exporter_code=group["exporter_code"],
                foreign_supplier=group["foreign_supplier"],
                operation_nature=(
                    "Importação de mercadorias"
                    if mixed
                    else self._default_operation_nature(purposes[0])
                ),
                item_purposes=purposes,
                mixed_import_purposes=mixed,
                items_count=len(group["items"]),
                customs_value=group_customs_value.quantize(Decimal("0.01")),
                allocated_shared_costs=group_costs,
                totals={
                    "customs_value": self._money_text(group_customs_value),
                    "shared_costs": self._money_text(group_cost_total),
                    "planned_value": self._money_text(
                        group_customs_value + group_cost_total
                    ),
                },
                status="planned",
                created_at=now,
                updated_at=now,
            )
            db.session.add(document)
            db.session.flush()
            document_rows.append(document)

            for planned in group["items"]:
                source = planned["source"]
                classification = planned["classification"]
                db.session.add(
                    NfePlannedDocumentItem(
                        organization_id=self._require_organization_id(),
                        document_plan_id=plan.id,
                        planned_document_id=document.id,
                        duimp_snapshot_id=snapshot.id,
                        item_classification_id=classification.id,
                        duimp_item_number=str(source.get("number") or ""),
                        exporter_key=exporter_key,
                        exporter_code=group["exporter_code"],
                        import_purpose=classification.import_purpose,
                        cfop=classification.cfop,
                        customs_value=planned["customs_value"].quantize(
                            Decimal("0.01")
                        ),
                        allocated_shared_costs={
                            name: self._money_text(value)
                            for name, value in planned["allocations"].items()
                        },
                        raw_source_payload=source,
                        created_at=now,
                        updated_at=now,
                    )
                )

        total_customs_value = sum(weights, Decimal("0"))
        total_shared_costs = sum(
            (self._decimal(value) for value in shared_costs.values()),
            Decimal("0"),
        )
        checks = []
        for name, expected_text in shared_costs.items():
            allocated = sum(allocations[name], Decimal("0"))
            expected = self._decimal(expected_text)
            difference = expected - allocated
            checks.append(
                {
                    "name": name,
                    "expected": self._money_text(expected),
                    "allocated": self._money_text(allocated),
                    "difference": self._money_text(difference),
                    "balanced": difference == Decimal("0"),
                }
            )
        plan.totals = {
            "documents_count": len(document_rows),
            "items_count": len(source_items),
            "customs_value": self._money_text(total_customs_value),
            "shared_costs": self._money_text(total_shared_costs),
            "planned_value": self._money_text(
                total_customs_value + total_shared_costs
            ),
        }
        plan.reconciliation = {
            "balanced": all(check["balanced"] for check in checks),
            "checks": checks,
            "unassigned_items": 0,
        }
        process.updated_at = now
        db.session.flush()
        return self._serialize_document_plan(plan)

    @staticmethod
    def _allocate_shared_cost_to_items(
        total: Decimal,
        weights: list[Decimal],
    ) -> list[Decimal]:
        money = Decimal("0.01")
        total = total.quantize(money)
        if not weights:
            return []
        if total == 0:
            return [Decimal("0.00") for _ in weights]
        weight_total = sum(weights, Decimal("0"))
        if weight_total <= 0:
            raise ValueError(
                "Não é possível ratear custos sem valor aduaneiro positivo."
            )
        total_cents = int(total / money)
        allocations = [
            int(
                (Decimal(total_cents) * weight / weight_total).quantize(
                    Decimal("1"),
                    rounding=ROUND_DOWN,
                )
            )
            for weight in weights
        ]
        remainder = total_cents - sum(allocations)
        largest_item = max(
            range(len(weights)),
            key=lambda index: (weights[index], -index),
        )
        allocations[largest_item] += remainder
        return [Decimal(cents) * money for cents in allocations]

    def _document_exporter(
        self,
        item: dict[str, Any],
        duimp: dict[str, Any],
    ) -> tuple[str, str | None, dict[str, Any] | None]:
        supplier = item.get("exporter")
        supplier = supplier if isinstance(supplier, dict) else None
        exporter_code = self._empty_to_none(
            item.get("exporter_code")
            or (supplier or {}).get("code")
            or (supplier or {}).get("codigo")
            or duimp.get("exporter_code")
        )
        suppliers = [
            row
            for row in (duimp.get("foreign_suppliers") or [])
            if isinstance(row, dict)
        ]
        if supplier is None and exporter_code:
            supplier = next(
                (
                    row
                    for row in suppliers
                    if self._empty_to_none(row.get("code") or row.get("codigo"))
                    == exporter_code
                ),
                None,
            )
        if supplier is None and len(suppliers) == 1:
            supplier = suppliers[0]
        if supplier is None and isinstance(duimp.get("foreign_supplier"), dict):
            supplier = duimp["foreign_supplier"]

        foreign_id = self._empty_to_none(
            (supplier or {}).get("foreign_tax_id")
            or (supplier or {}).get("foreign_id")
            or (supplier or {}).get("tin")
        )
        name = self._empty_to_none(
            (supplier or {}).get("name")
            or (supplier or {}).get("legal_name")
            or (supplier or {}).get("razaoSocial")
        )
        if exporter_code:
            key = f"code:{exporter_code}"
        elif foreign_id:
            key = f"foreign_id:{foreign_id}"
        elif name:
            key = f"name:{name.upper()}"
        else:
            key = "single:default"
        return key, exporter_code, supplier

    def _serialize_document_plan(
        self,
        plan: NfeDocumentPlan,
    ) -> dict[str, Any]:
        documents = [
            self._serialize_planned_document(document)
            for document in plan.documents
        ]
        progress = {
            "documents_count": len(documents),
            "drafts_count": sum(
                1 for document in documents if document["draft"]
            ),
            "xmls_count": sum(
                1
                for document in documents
                if (document["draft"] or {}).get("latest_xml")
            ),
            "xsd_valid_count": sum(
                1
                for document in documents
                if ((document["draft"] or {}).get("latest_xml") or {}).get(
                    "xsd_valid"
                ) is True
            ),
        }
        progress["all_drafts_created"] = bool(documents) and (
            progress["drafts_count"] == progress["documents_count"]
        )
        progress["all_xmls_generated"] = bool(documents) and (
            progress["xmls_count"] == progress["documents_count"]
        )
        progress["all_xmls_valid"] = bool(documents) and (
            progress["xsd_valid_count"] == progress["documents_count"]
        )
        return {
            "id": str(plan.id),
            "process_id": str(plan.import_process_id),
            "snapshot_id": str(plan.duimp_snapshot_id),
            "version_number": plan.version_number,
            "status": plan.status,
            "allocation_basis": plan.allocation_basis,
            "shared_costs": plan.shared_costs or {},
            "totals": plan.totals or {},
            "reconciliation": plan.reconciliation or {},
            "master": {
                "type": "managerial",
                "is_fiscal_document": False,
                "has_number": False,
                "has_access_key": False,
                "has_xml": False,
            },
            "progress": progress,
            "documents": documents,
            "created_by": (
                {
                    "id": str(plan.created_by.id),
                    "name": plan.created_by.nome,
                }
                if plan.created_by
                else None
            ),
            "created_at": self._iso(plan.created_at),
            "updated_at": self._iso(plan.updated_at),
        }

    @staticmethod
    def _latest_active_document_draft(
        document: NfePlannedDocument,
    ) -> NfeDraft | None:
        return next(
            (
                draft
                for draft in reversed(document.drafts)
                if draft.deleted_at is None
            ),
            None,
        )

    def _serialize_planned_document(
        self,
        document: NfePlannedDocument,
    ) -> dict[str, Any]:
        latest_draft = self._latest_active_document_draft(document)
        draft_summary = None
        derived_status = document.status
        if latest_draft:
            latest_xml = (
                NfeXmlVersion.query.filter(
                    NfeXmlVersion.nfe_draft_id == latest_draft.id,
                    NfeXmlVersion.xml_type == NfeXmlType.UNSIGNED.value,
                )
                .order_by(NfeXmlVersion.version_number.desc())
                .first()
            )
            if latest_xml and latest_xml.xsd_valid is True:
                derived_status = "xsd_validated"
            elif latest_xml and latest_xml.xsd_valid is False:
                derived_status = "xsd_invalid"
            elif latest_xml:
                derived_status = "xml_generated"
            elif latest_draft.validation_errors:
                derived_status = "correction_required"
            else:
                derived_status = "draft_ready"
            draft_summary = {
                "id": str(latest_draft.id),
                "status": self._enum_value(latest_draft.status),
                "number": latest_draft.number,
                "series": latest_draft.series,
                "access_key": latest_draft.access_key,
                "validation_errors": latest_draft.validation_errors or [],
                "validation_warnings": latest_draft.validation_warnings or [],
                "latest_xml": (
                    {
                        "id": str(latest_xml.id),
                        "version_number": latest_xml.version_number,
                        "xml_type": self._enum_value(latest_xml.xml_type),
                        "xsd_valid": latest_xml.xsd_valid,
                        "xsd_errors": latest_xml.xsd_errors or [],
                        "generated_at": self._iso(latest_xml.generated_at),
                    }
                    if latest_xml
                    else None
                ),
                "created_at": self._iso(latest_draft.created_at),
                "updated_at": self._iso(latest_draft.updated_at),
            }
        return {
            "id": str(document.id),
            "ordinal": document.ordinal,
            "status": derived_status,
            "exporter_key": document.exporter_key,
            "exporter_code": document.exporter_code,
            "foreign_supplier": document.foreign_supplier,
            "operation_nature": document.operation_nature,
            "item_purposes": document.item_purposes or [],
            "mixed_import_purposes": document.mixed_import_purposes,
            "items_count": document.items_count,
            "customs_value": self._money_text(document.customs_value),
            "allocated_shared_costs": document.allocated_shared_costs or {},
            "totals": document.totals or {},
            "draft": draft_summary,
            "items": [
                {
                    "id": str(item.id),
                    "duimp_item_number": item.duimp_item_number,
                    "exporter_code": item.exporter_code,
                    "import_purpose": item.import_purpose,
                    "cfop": item.cfop,
                    "customs_value": self._money_text(item.customs_value),
                    "allocated_shared_costs": item.allocated_shared_costs or {},
                }
                for item in document.items
            ],
        }

    def generate_child_drafts(
        self,
        process: ImportProcess,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = self._snapshot_for_process(
            process,
            payload.get("duimp_snapshot_id"),
        )
        plan = (
            self.document_plan_query_for_current_user()
            .filter(
                NfeDocumentPlan.import_process_id == process.id,
                NfeDocumentPlan.duimp_snapshot_id == snapshot.id,
                NfeDocumentPlan.status == "planned",
            )
            .order_by(NfeDocumentPlan.version_number.desc())
            .first()
        )
        if plan is None:
            raise ValueError("Gere e revise o plano de notas antes dos rascunhos.")

        created = []
        reused = []
        for document in plan.documents:
            existing = self._latest_active_document_draft(document)
            if existing:
                reused.append(str(existing.id))
                continue
            child_payload = deepcopy(payload)
            child_payload["duimp_snapshot_id"] = snapshot.id
            child_payload.setdefault(
                "import_purpose",
                (document.item_purposes or [ImportPurpose.RESALE.value])[0],
            )
            result = self.create_nfe_draft_from_duimp(
                process,
                child_payload,
                planned_document=document,
            )
            created.append(str(result["draft"].id))
            document.status = "drafted"
            document.updated_at = datetime.utcnow()

        plan.updated_at = datetime.utcnow()
        db.session.flush()
        return {
            "created_draft_ids": created,
            "reused_draft_ids": reused,
            "plan": self._serialize_document_plan(plan),
        }

    def generate_and_validate_child_xmls(
        self,
        process: ImportProcess,
        snapshot_id=None,
    ) -> dict[str, Any]:
        snapshot = self._snapshot_for_process(process, snapshot_id)
        plan = (
            self.document_plan_query_for_current_user()
            .filter(
                NfeDocumentPlan.import_process_id == process.id,
                NfeDocumentPlan.duimp_snapshot_id == snapshot.id,
                NfeDocumentPlan.status == "planned",
            )
            .order_by(NfeDocumentPlan.version_number.desc())
            .first()
        )
        if plan is None:
            raise ValueError("Plano de notas ativo não encontrado.")

        results = []
        for document in plan.documents:
            draft = self._latest_active_document_draft(document)
            if draft is None:
                results.append({
                    "planned_document_id": str(document.id),
                    "success": False,
                    "message": "Gere o rascunho desta NF-e filha primeiro.",
                })
                continue
            try:
                latest_xml = (
                    NfeXmlVersion.query.filter(
                        NfeXmlVersion.nfe_draft_id == draft.id,
                        NfeXmlVersion.xml_type == NfeXmlType.UNSIGNED.value,
                    )
                    .order_by(NfeXmlVersion.version_number.desc())
                    .first()
                )
                xml_is_current = bool(
                    latest_xml
                    and (
                        not draft.updated_at
                        or not latest_xml.generated_at
                        or draft.updated_at <= latest_xml.generated_at
                    )
                )
                if not xml_is_current:
                    latest_xml = self.generate_unsigned_xml(draft)
                validation = self.validate_xml_version(draft, latest_xml)
                document.status = (
                    "xsd_validated" if validation.is_valid else "xsd_invalid"
                )
                document.updated_at = datetime.utcnow()
                results.append({
                    "planned_document_id": str(document.id),
                    "draft_id": str(draft.id),
                    "xml_version_id": str(latest_xml.id),
                    "success": validation.is_valid,
                    "xsd_valid": validation.is_valid,
                    "xsd_errors": validation.errors,
                })
            except ValueError as exc:
                document.status = "correction_required"
                document.updated_at = datetime.utcnow()
                results.append({
                    "planned_document_id": str(document.id),
                    "draft_id": str(draft.id),
                    "success": False,
                    "message": str(exc),
                })

        all_valid = bool(results) and all(
            result.get("xsd_valid") is True for result in results
        )
        process.status = (
            ImportProcessStatus.XML_VALIDATED.value
            if all_valid
            else ImportProcessStatus.XML_VALIDATION_FAILED.value
        )
        process.updated_at = datetime.utcnow()
        plan.updated_at = datetime.utcnow()
        db.session.flush()
        return {
            "all_valid": all_valid,
            "results": results,
            "plan": self._serialize_document_plan(plan),
        }

    @staticmethod
    def _money_text(value: Any) -> str:
        return f"{Decimal(str(value or '0')).quantize(Decimal('0.01')):.2f}"

    # ------------------------------------------------------------------
    # NFe draft
    # ------------------------------------------------------------------
    def create_nfe_draft_from_duimp(
        self,
        process: ImportProcess,
        payload: dict[str, Any],
        *,
        planned_document: NfePlannedDocument | None = None,
    ) -> dict[str, Any]:
        payload = self._json_compatible(payload)
        payload["series"] = payload.get("series") or self.DEFAULT_NFE_SERIES
        fiscal_profile = self.get_importer_fiscal_profile(process.importer_id)
        snapshot_id = payload.get("duimp_snapshot_id")
        if snapshot_id:
            snapshot = (
                self.snapshot_query_for_current_user()
                .filter(
                    DuimpSnapshot.id == snapshot_id,
                    DuimpSnapshot.import_process_id == process.id,
                )
                .first()
            )
            if snapshot is None:
                raise ValueError("Snapshot da DUIMP não encontrado para este processo.")
            normalized = snapshot.normalized_payload or self.normalize_duimp_payload(
                snapshot.raw_payload
            )
        else:
            fetch_result = self.fetch_duimp_for_process(process, payload)
            normalized = fetch_result["normalized"]
            snapshot = fetch_result["snapshot"]

        preallocated_costs = None
        if planned_document is not None:
            if (
                planned_document.document_plan.import_process_id != process.id
                or planned_document.document_plan.duimp_snapshot_id != snapshot.id
                or planned_document.document_plan.status != "planned"
            ):
                raise ValueError(
                    "A NF-e filha não pertence ao plano ativo deste processo."
                )
            normalized = deepcopy(normalized)
            normalized["items"] = [
                deepcopy(item.raw_source_payload or {})
                for item in planned_document.items
            ]
            normalized["foreign_supplier"] = deepcopy(
                planned_document.foreign_supplier
            )
            normalized["foreign_suppliers"] = [
                deepcopy(planned_document.foreign_supplier)
            ] if planned_document.foreign_supplier else []
            normalized["exporter_code"] = planned_document.exporter_code
            normalized["afrmm_value"] = (
                planned_document.allocated_shared_costs or {}
            ).get("afrmm", "0.00")
            # Totais declarados na DUIMP pertencem ao processo inteiro. Cada
            # filha é conciliada pelos próprios itens e pelo rateio persistido.
            normalized["tax_totals"] = {}
            fiscal_references = deepcopy(
                normalized.get("automation_fiscal_references") or {}
            )
            fiscal_references.pop("icms", None)
            normalized["automation_fiscal_references"] = fiscal_references
            payload["import_purpose"] = (
                (planned_document.item_purposes or [None])[0]
                or payload.get("import_purpose")
            )
            payload["foreign_supplier"] = deepcopy(
                planned_document.foreign_supplier
            )
            payload["additional_costs"] = deepcopy(
                planned_document.allocated_shared_costs or {}
            )
            payload["document"] = self._merge_defaults(
                payload.get("document"),
                {"operation_nature": planned_document.operation_nature},
            )
            preallocated_costs = {
                item.duimp_item_number: deepcopy(
                    item.allocated_shared_costs or {}
                )
                for item in planned_document.items
            }

        exporter_keys = {
            self._document_exporter(item, normalized)[0]
            for item in (normalized.get("items") or [])
        }
        if planned_document is None and len(exporter_keys) > 1:
            raise ValueError(
                "A DUIMP possui múltiplos exportadores. Revise o plano de notas; "
                "os rascunhos independentes de cada NF-e filha serão gerados na "
                "próxima etapa do fluxo."
            )

        classification_map = self._item_classification_map(process, snapshot)
        if planned_document is not None:
            planned_item_numbers = {
                item.duimp_item_number for item in planned_document.items
            }
            classification_map = {
                number: classification
                for number, classification in classification_map.items()
                if number in planned_item_numbers
            }
        tax_rules: list[ClientImportTaxRule] = []
        tax_rule = None
        tax_configuration = payload.get("tax_configuration")

        if classification_map:
            classification_state = self.get_item_classification_state(
                process,
                snapshot.id,
            )
            if not classification_state["ready_for_draft"]:
                raise ValueError(
                    "Todos os itens da DUIMP precisam ter finalidade, regra "
                    "tributária e CFOP antes da criação do rascunho."
                )
            tax_rules = list(
                {
                    row.tax_rule.id: row.tax_rule
                    for row in classification_map.values()
                    if row.tax_rule
                }.values()
            )
            tax_rule = tax_rules[0]
            tax_configuration = deepcopy(tax_rule.configuration_json or {})
            payload["import_purpose"] = (
                payload.get("import_purpose")
                or next(
                    (
                        row.import_purpose
                        for row in classification_map.values()
                        if row.import_purpose
                    ),
                    ImportPurpose.RESALE.value,
                )
            )
        elif tax_configuration is None:
            payload["import_purpose"] = (
                payload.get("import_purpose") or ImportPurpose.RESALE.value
            )
            missing_rules = []
            for source_item in normalized.get("items", []):
                item_number = str(source_item.get("number") or "")
                item_ncm = self._digits(source_item.get("ncm"))
                resolution = self.resolve_import_tax_rule(
                    client_id=process.importer_id,
                    issuer_state=fiscal_profile.state,
                    tax_regime=fiscal_profile.tax_regime,
                    import_purpose=payload["import_purpose"],
                    import_modality=normalized.get("import_modality"),
                    ncm=item_ncm,
                    reference_date=self._date_value(
                        normalized.get("registration_date")
                    ),
                    rule_id=payload.get("tax_rule_id"),
                )
                resolved_rule = resolution["rule"]
                if resolved_rule is None:
                    missing_rules.append(
                        {"duimp_item_number": item_number, "ncm": item_ncm}
                    )
                    continue
                configuration = resolved_rule.configuration_json or {}
                configured_cfop = self._digits(configuration.get("cfop"))
                classification = NfeItemClassification(
                    import_purpose=payload["import_purpose"],
                    tax_rule_id=resolved_rule.id,
                    cfop=(
                        configured_cfop
                        if len(configured_cfop) == 4
                        else self._resolve_cfop(payload["import_purpose"])
                    ),
                    source="automatic",
                    tax_rule_snapshot=self._tax_rule_snapshot(
                        resolved_rule,
                        ncm=item_ncm,
                        selection=resolution["selection"],
                    ),
                )
                classification.tax_rule = resolved_rule
                classification_map[item_number] = classification

            if missing_rules:
                details = ", ".join(
                    f"item {item['duimp_item_number']} (NCM {item['ncm']})"
                    for item in missing_rules
                )
                raise ValueError(
                    "Nenhuma regra fiscal aplicável foi encontrada para: "
                    f"{details}. Cadastre as regras antes de gerar o rascunho."
                )
            tax_rules = list(
                {
                    row.tax_rule.id: row.tax_rule
                    for row in classification_map.values()
                    if row.tax_rule
                }.values()
            )
            tax_rule = tax_rules[0]
            tax_configuration = deepcopy(tax_rule.configuration_json or {})

        additional_costs = self._merge_defaults(
            tax_rule.additional_cost_defaults if tax_rule else None,
            normalized.get("automation_additional_costs"),
        )
        additional_costs.update(payload.get("additional_costs") or {})
        transport = self._merge_defaults(
            tax_rule.transport_defaults if tax_rule else None,
            payload.get("transport"),
        )
        payment = self._merge_defaults(
            tax_rule.payment_defaults if tax_rule else None,
            payload.get("payment"),
        )
        document_options = self._merge_defaults(
            tax_configuration.get("document_defaults"),
            payload.get("document"),
        )
        item_defaults = self._merge_defaults(
            tax_configuration.get("item_defaults"),
            payload.get("item_defaults"),
        )
        additional_info = self._merge_defaults(
            tax_configuration.get("additional_info_defaults"),
            payload.get("additional_info"),
        )

        fiscal_payload = self.map_duimp_to_nfe_payload(
            duimp=normalized,
            process=process,
            fiscal_profile=fiscal_profile,
            environment=payload["environment"],
            series=payload["series"],
            number=payload.get("number"),
            import_purpose=payload["import_purpose"],
            tax_configuration=tax_configuration,
            additional_costs=additional_costs,
            foreign_supplier=payload.get("foreign_supplier"),
            duimp_overrides=payload.get("duimp_overrides"),
            document_options=document_options,
            item_defaults=item_defaults,
            transport=transport,
            payment=payment,
            additional_info=additional_info,
            item_classifications=classification_map or None,
            preallocated_costs=preallocated_costs,
        )
        fiscal_payload["source"]["tax_configuration_source"] = (
            "client_import_tax_rule" if tax_rule else "request"
        )
        fiscal_payload["source"]["tax_rule_id"] = (
            str(tax_rule.id) if tax_rule else None
        )
        fiscal_payload["source"]["tax_rule_ids"] = [
            str(rule.id) for rule in tax_rules
        ]

        validation = self.validate_nfe_payload(fiscal_payload)
        now = datetime.utcnow()
        draft_status = NfeDraftStatus.READY_FOR_XML.value if validation.is_valid else NfeDraftStatus.VALIDATION_FAILED.value

        draft = NfeDraft(
            organization_id=self._require_organization_id(),
            import_process_id=process.id,
            importer_id=process.importer_id,
            duimp_snapshot_id=snapshot.id,
            planned_document_id=(
                planned_document.id if planned_document else None
            ),
            planned_document=planned_document,
            model=NfeModel.NFE.value,
            purpose=NfePurpose.NORMAL.value,
            operation_type=NfeOperationType.ENTRY.value,
            environment=payload["environment"],
            series=payload["series"],
            number=payload.get("number"),
            status=draft_status,
            fiscal_payload=fiscal_payload,
            validation_errors=validation.errors or None,
            validation_warnings=validation.warnings or None,
            created_at=now,
            updated_at=now,
        )
        db.session.add(draft)
        db.session.flush()

        for item_payload in fiscal_payload["items"]:
            item = self._build_draft_item(draft=draft, item_payload=item_payload)
            db.session.add(item)

        process.duimp_number = normalized["number"]
        process.duimp_version = normalized.get("version")
        process.status = (
            ImportProcessStatus.DRAFT_READY.value
            if validation.is_valid
            else ImportProcessStatus.DRAFT_VALIDATION_FAILED.value
        )
        process.updated_at = now
        db.session.flush()

        return {
            "draft": draft,
            "snapshot": snapshot,
            "validation": validation.to_dict(),
            "tax_rule": self._tax_rule_to_dict(tax_rule) if tax_rule else None,
            "tax_rules": [
                self._tax_rule_to_dict(rule)
                for rule in tax_rules
            ],
        }

    def get_nfe_draft_detail(self, draft: NfeDraft) -> dict[str, Any]:
        items = (
            NfeDraftItem.query.filter(NfeDraftItem.nfe_draft_id == draft.id)
            .order_by(NfeDraftItem.item_number.asc())
            .all()
        )
        xml_versions = (
            NfeXmlVersion.query.filter(NfeXmlVersion.nfe_draft_id == draft.id)
            .order_by(NfeXmlVersion.version_number.desc())
            .all()
        )
        audit_trail = list((draft.fiscal_payload or {}).get("audit_trail") or [])
        for item in items:
            icms = ((item.tax_payload or {}).get("icms") or {})
            for event in icms.get("adjustment_history") or []:
                audit_trail.append(
                    {
                        **event,
                        "section": "taxes",
                        "item_id": str(item.id),
                        "item_number": item.item_number,
                    }
                )
        audit_trail.sort(
            key=lambda event: str(event.get("changed_at") or ""),
            reverse=True,
        )
        return {
            "draft": draft,
            "items": items,
            "xml_versions": xml_versions,
            "audit_trail": audit_trail,
        }

    def update_draft_metadata(
        self,
        draft: NfeDraft,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._assert_draft_editable(draft)

        transport_update = payload.get("transport") or {}
        replace_transport_carrier = "carrier" in transport_update
        carrier_id = transport_update.pop("carrier_id", None)
        if carrier_id:
            replace_transport_carrier = True
            carrier = NfeCarrier.query.filter(
                NfeCarrier.id == carrier_id,
                NfeCarrier.organization_id == self.organization_id,
                NfeCarrier.active.is_(True),
            ).first()
            if not carrier:
                raise ValueError(
                    "Transportadora cadastrada não encontrada ou inativa."
                )
            from app.services.nfe_carrier import NfeCarrierService

            transport_update["carrier"] = NfeCarrierService(
                self.current_user
            ).snapshot(carrier)

        # Marshmallow desserializa fields.Decimal como Decimal. As colunas JSON
        # do PostgreSQL aceitam apenas tipos JSON nativos, então a normalização
        # precisa acontecer antes de qualquer consulta que dispare autoflush.
        payload = self._json_compatible(payload)
        rows = (
            NfeDraftItem.query
            .filter(NfeDraftItem.nfe_draft_id == draft.id)
            .order_by(NfeDraftItem.item_number.asc())
            .all()
        )
        now = datetime.utcnow()
        fiscal_payload = deepcopy(draft.fiscal_payload or {})
        previous_additional_costs = deepcopy(
            fiscal_payload.get("additional_costs") or {}
        )

        for section in ("document", "transport", "payment"):
            if section in payload:
                fiscal_payload[section] = self._merge_defaults(
                    fiscal_payload.get(section),
                    payload[section],
                )
        if replace_transport_carrier:
            fiscal_payload.setdefault("transport", {})["carrier"] = deepcopy(
                payload["transport"].get("carrier")
            )

        if "issuer" in payload:
            issuer_update = deepcopy(payload["issuer"] or {})
            if "state_registration" in issuer_update:
                state_registration = str(
                    issuer_update.get("state_registration") or ""
                ).strip().upper()
                normalized_ie = (
                    "ISENTO"
                    if state_registration == "ISENTO"
                    else self._digits(state_registration)
                )
                if normalized_ie != "ISENTO" and not (
                    2 <= len(normalized_ie) <= 14
                ):
                    raise ValueError(
                        "A inscrição estadual deve ser ISENTO ou conter "
                        "entre 2 e 14 dígitos."
                    )
                fiscal_payload.setdefault("issuer", {})[
                    "state_registration"
                ] = normalized_ie

        if "foreign_supplier" in payload:
            supplier_update = deepcopy(payload["foreign_supplier"] or {})
            recipient_update: dict[str, Any] = {}
            if supplier_update.get("legal_name") not in (None, ""):
                recipient_update["legal_name"] = supplier_update["legal_name"]
            if "foreign_id" in supplier_update:
                recipient_update["foreign_id"] = self._empty_to_none(
                    supplier_update.get("foreign_id")
                )

            address_update = deepcopy(supplier_update.get("address") or {})
            for field in (
                "country_code",
                "country_name",
                "country_iso_alpha_2",
            ):
                if supplier_update.get(field) not in (None, ""):
                    address_update[field] = supplier_update[field]
            if address_update:
                recipient_update["address"] = address_update

            if recipient_update:
                fiscal_payload["recipient"] = self._merge_defaults(
                    fiscal_payload.get("recipient"),
                    recipient_update,
                )

        volume_update = (
            (payload.get("transport") or {}).get("volume") or {}
        )
        if volume_update.get("net_weight") not in (None, ""):
            fiscal_payload.setdefault("transport", {}).setdefault(
                "volume", {}
            )["net_weight_source"] = "operator_override"

        if "additional_info" in payload:
            additional_update = deepcopy(payload["additional_info"] or {})
            legal_text = str(
                additional_update.pop("legal_text", "") or ""
            ).strip()
            if additional_update.get("automatic_summary") is True:
                current_additional_info = (
                    fiscal_payload.get("additional_info") or {}
                )
                summary_options = {
                    "fiscal": current_additional_info.get("fiscal"),
                    **additional_update,
                    "legal_text": legal_text,
                }
                additional_info = self._build_import_additional_info(
                    duimp=fiscal_payload.get("duimp") or {},
                    totals=fiscal_payload.get("totals") or {},
                    additional_costs=(
                        fiscal_payload.get("additional_costs") or {}
                    ),
                    options=summary_options,
                )
            else:
                additional_info = self._merge_defaults(
                    fiscal_payload.get("additional_info"),
                    additional_update,
                )
            if (
                legal_text
                and additional_update.get("automatic_summary") is not True
            ):
                complementary = str(
                    additional_info.get("complementary") or ""
                ).strip()
                if legal_text not in complementary:
                    additional_info["complementary"] = " ".join(
                        part for part in (complementary, legal_text) if part
                    )
                additional_info["legal_text_present"] = True
            fiscal_payload["additional_info"] = additional_info

        item_defaults = payload.get("item_defaults") or {}
        for row in rows:
            for field in ("commercial_unit", "taxable_unit"):
                if item_defaults.get(field):
                    setattr(row, field, item_defaults[field])
                    row.updated_at = now

        had_xml_versions = (
            NfeXmlVersion.query.filter(
                NfeXmlVersion.nfe_draft_id == draft.id
            ).first()
            is not None
        )
        if "additional_costs" in payload:
            next_costs = {
                **previous_additional_costs,
                **(payload.get("additional_costs") or {}),
            }
            fiscal_payload["additional_costs"] = next_costs
            self._recalculate_draft_rows(
                rows,
                fiscal_payload=fiscal_payload,
                additional_costs=next_costs,
            )
            if self._json_compatible(previous_additional_costs) != next_costs:
                fiscal_payload.setdefault("audit_trail", []).append(
                    self._draft_audit_event(
                        section="additional_costs",
                        reason="Despesas de importação atualizadas no editor.",
                        previous=previous_additional_costs,
                        current=next_costs,
                        changed_at=now,
                    )
                )

        draft.fiscal_payload = fiscal_payload
        self._refresh_draft_payload_from_items(draft)
        validation = self.validate_nfe_payload(draft.fiscal_payload)
        draft.validation_errors = validation.errors or None
        draft.validation_warnings = validation.warnings or None
        draft.status = (
            NfeDraftStatus.READY_FOR_XML.value
            if validation.is_valid
            else NfeDraftStatus.VALIDATION_FAILED.value
        )
        draft.updated_at = now

        process = (
            self.import_process_query_for_current_user()
            .filter(ImportProcess.id == draft.import_process_id)
            .first()
        )
        if process:
            process.status = (
                ImportProcessStatus.DRAFT_READY.value
                if validation.is_valid
                else ImportProcessStatus.DRAFT_VALIDATION_FAILED.value
            )
            process.updated_at = now

        db.session.flush()
        return {
            "draft": draft,
            "items": rows,
            "validation": validation.to_dict(),
            "requires_new_xml": had_xml_versions,
        }

    def update_draft_item(self, draft: NfeDraft, item: NfeDraftItem, payload: dict[str, Any]) -> NfeDraftItem:
        self._assert_draft_editable(draft)
        allowed_fields = [
            "product_code",
            "description",
            "ncm",
            "cfop",
            "cest",
            "commercial_unit",
            "commercial_quantity",
            "commercial_unit_value",
            "taxable_unit",
            "taxable_quantity",
            "taxable_unit_value",
            "product_value",
            "freight_value",
            "insurance_value",
            "discount_value",
            "other_value",
            "import_payload",
        ]
        for field in allowed_fields:
            if field in payload:
                setattr(item, field, payload[field])

        item.updated_at = datetime.utcnow()
        self._refresh_draft_payload_from_items(draft)
        validation = self.validate_nfe_payload(draft.fiscal_payload)
        draft.validation_errors = validation.errors or None
        draft.validation_warnings = validation.warnings or None
        draft.status = NfeDraftStatus.READY_FOR_XML.value if validation.is_valid else NfeDraftStatus.VALIDATION_FAILED.value
        draft.updated_at = datetime.utcnow()
        db.session.flush()
        return item

    def adjust_draft_item_tax(
        self,
        draft: NfeDraft,
        item: NfeDraftItem,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._assert_draft_editable(draft)
        had_xml_versions = NfeXmlVersion.query.filter(
            NfeXmlVersion.nfe_draft_id == draft.id
        ).first() is not None
        now = datetime.utcnow()
        source = payload.get("source") or "manual_adjustment"
        reason = str(payload["reason"]).strip()
        previous_cfop = item.cfop
        previous_taxes = deepcopy(item.tax_payload or {})
        previous_icms = deepcopy(previous_taxes.get("icms") or {})

        if payload.get("cfop"):
            item.cfop = payload["cfop"]

        if source == "tax_rule":
            if not item.tax_rule:
                raise ValueError(
                    "O item não possui uma regra tributária vinculada para reaplicar."
                )
            calculated = self._calculate_row_from_tax_rule(draft, item)
            next_taxes = deepcopy(calculated.get("tax_payload") or {})
            next_icms = deepcopy(next_taxes.get("icms") or {})
            next_icms["calculation_source"] = "tax_rule"
        else:
            adjustment = self._json_compatible(payload["icms"])
            next_taxes = deepcopy(previous_taxes)
            next_icms = self._manual_icms_values(previous_icms, adjustment)

        event = self._draft_audit_event(
            section="taxes",
            reason=reason,
            previous={"cfop": previous_cfop, "icms": previous_icms},
            current={"cfop": item.cfop, "icms": next_icms},
            changed_at=now,
        )
        event["source"] = source
        history = list(previous_icms.get("adjustment_history") or [])
        history.append(event)
        next_icms["adjustment_history"] = history
        next_icms["manual_adjustment"] = (
            {
                "reason": reason,
                "changed_at": event["changed_at"],
                "changed_by_user_id": event["changed_by_user_id"],
                "changed_by_name": event["changed_by_name"],
                "values": self._json_compatible(payload.get("icms") or {}),
            }
            if source == "manual_adjustment"
            else None
        )
        next_taxes["icms"] = next_icms
        item.tax_payload = next_taxes
        item.updated_at = now

        self._refresh_draft_payload_from_items(draft)
        validation = self.validate_nfe_payload(draft.fiscal_payload)
        draft.validation_errors = validation.errors or None
        draft.validation_warnings = validation.warnings or None
        draft.status = (
            NfeDraftStatus.READY_FOR_XML.value
            if validation.is_valid
            else NfeDraftStatus.VALIDATION_FAILED.value
        )
        draft.updated_at = now
        db.session.flush()
        return {
            "draft": draft,
            "item": item,
            "validation": validation.to_dict(),
            "audit": event,
            "requires_new_xml": had_xml_versions,
        }

    def soft_delete_draft(self, draft: NfeDraft, *, reason: str) -> dict[str, Any]:
        current_status = self._enum_value(draft.status)
        signed_xml_exists = NfeXmlVersion.query.filter(
            NfeXmlVersion.nfe_draft_id == draft.id,
            NfeXmlVersion.xml_type.in_([
                NfeXmlType.SIGNED.value,
                NfeXmlType.AUTHORIZED.value,
            ]),
        ).first() is not None
        if signed_xml_exists or current_status in {
            NfeDraftStatus.SIGNED.value,
            NfeDraftStatus.TRANSMITTED.value,
            NfeDraftStatus.AUTHORIZED.value,
            NfeDraftStatus.CANCELLED.value,
        }:
            raise ValueError(
                "Rascunhos assinados, transmitidos, autorizados ou cancelados "
                "não podem ser excluídos nem arquivados."
            )

        now = datetime.utcnow()
        has_xml = NfeXmlVersion.query.filter(
            NfeXmlVersion.nfe_draft_id == draft.id
        ).first() is not None
        has_reserved_number = bool(draft.number or draft.access_key)
        mode = "archived" if has_reserved_number or has_xml else "deleted"
        fiscal_payload = deepcopy(draft.fiscal_payload or {})
        if mode == "archived":
            fiscal_payload["numbering_disposition"] = {
                "status": "pending_inutilization_review",
                "number": draft.number,
                "series": draft.series,
                "access_key": draft.access_key,
            }
        fiscal_payload.setdefault("audit_trail", []).append(
            self._draft_audit_event(
                section="draft_lifecycle",
                reason=reason,
                previous={"deletion_mode": None},
                current={"deletion_mode": mode},
                changed_at=now,
            )
        )
        draft.fiscal_payload = fiscal_payload
        draft.deleted_at = now
        draft.deleted_by_user_id = self.user_id
        draft.deletion_reason = reason.strip()
        draft.deletion_mode = mode
        draft.updated_at = now
        db.session.flush()
        return {
            "draft_id": str(draft.id),
            "deletion_mode": mode,
            "deleted_at": now.isoformat() + "Z",
            "number": draft.number,
            "series": draft.series,
            "access_key": draft.access_key,
            "requires_inutilization_review": mode == "archived",
            "message": (
                "Rascunho arquivado. A numeração reservada foi mantida para revisão de inutilização."
                if mode == "archived"
                else "Rascunho excluído logicamente. Nenhuma numeração fiscal havia sido reservada."
            ),
        }

    def _assert_draft_editable(self, draft: NfeDraft) -> None:
        if draft.deleted_at:
            raise ValueError("Rascunho excluído ou arquivado não pode ser alterado.")
        current_status = self._enum_value(draft.status)
        if current_status in {
            NfeDraftStatus.SIGNED.value,
            NfeDraftStatus.TRANSMITTED.value,
            NfeDraftStatus.AUTHORIZED.value,
            NfeDraftStatus.CANCELLED.value,
        }:
            raise ValueError(
                "Rascunho assinado, transmitido, autorizado ou cancelado "
                "não pode mais ser alterado."
            )

    def _draft_audit_event(
        self,
        *,
        section: str,
        reason: str,
        previous: dict[str, Any],
        current: dict[str, Any],
        changed_at: datetime,
    ) -> dict[str, Any]:
        return {
            "section": section,
            "reason": reason,
            "previous": self._json_compatible(previous),
            "current": self._json_compatible(current),
            "changed_by_user_id": str(self.user_id) if self.user_id else None,
            "changed_by_name": getattr(self.current_user, "nome", None),
            "changed_at": changed_at.isoformat() + "Z",
        }

    def _manual_icms_values(
        self,
        previous: dict[str, Any],
        adjustment: dict[str, Any],
    ) -> dict[str, Any]:
        cst = str(adjustment["cst"]).zfill(2)
        base = self._decimal(adjustment.get("base")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        rate = self._decimal(adjustment.get("rate"))
        reduction = self._decimal(adjustment.get("reduction_rate"))
        deferment = self._decimal(adjustment.get("deferment_rate"))
        if cst in {"40", "41", "50"}:
            base = Decimal("0.00")
            rate = Decimal("0")
            value = Decimal("0.00")
            operation_value = None
            deferred_value = None
        else:
            if rate <= 0 or rate >= 100:
                raise ValueError(
                    "A alíquota do ICMS deve ser maior que zero e menor que 100."
                )
            operation_value = (base * rate / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            deferred_value = (
                operation_value * deferment / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            value = operation_value - deferred_value

        duimp_value = previous.get("duimp_value")
        next_icms = {
            **previous,
            "cst": cst,
            "base": format(base, ".2f"),
            "rate": None if cst in {"40", "41", "50"} else format(rate, ".4f"),
            "base_reduction_rate": (
                format(reduction, ".4f") if reduction else None
            ),
            "deferment_rate": (
                format(deferment, ".4f") if cst == "51" and deferment else None
            ),
            "operation_value": (
                format(operation_value, ".2f") if operation_value is not None else None
            ),
            "deferred_value": (
                format(deferred_value, ".2f") if deferred_value is not None else None
            ),
            "value": format(value, ".2f"),
            "diagnostic_only": False,
            "tax_treatment_confirmed": True,
            "calculation_source": "manual_adjustment",
        }
        if duimp_value not in (None, ""):
            next_icms["difference"] = format(
                (value - self._decimal(duimp_value)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                ".2f",
            )
        return next_icms

    def _calculate_row_from_tax_rule(
        self,
        draft: NfeDraft,
        item: NfeDraftItem,
    ) -> dict[str, Any]:
        source = self._item_to_payload(item)
        current_item = next(
            (
                row for row in (draft.fiscal_payload or {}).get("items") or []
                if str(row.get("item_number")) == str(item.item_number)
            ),
            {},
        )
        allocation = deepcopy(current_item.get("cost_allocation") or {})
        source["customs_value"] = current_item.get("customs_value") or source["product_value"]
        source["net_weight"] = current_item.get("net_weight") or "0"
        source["other_value"] = format(
            (
                self._decimal(source.get("other_value"))
                - sum(
                    (self._decimal(value) for value in allocation.values()),
                    Decimal("0"),
                )
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            ".2f",
        )
        calculated, _ = self.tax_calculator.calculate(
            [source],
            configuration=deepcopy(item.tax_rule.configuration_json or {}),
            additional_costs=allocation,
            preallocated_costs=[allocation],
        )
        return calculated[0]

    def validate_draft(self, draft: NfeDraft) -> ValidationResult:
        self._refresh_draft_payload_from_items(draft)
        validation = self.validate_nfe_payload(draft.fiscal_payload)
        draft.validation_errors = validation.errors or None
        draft.validation_warnings = validation.warnings or None
        draft.status = NfeDraftStatus.READY_FOR_XML.value if validation.is_valid else NfeDraftStatus.VALIDATION_FAILED.value
        draft.updated_at = datetime.utcnow()
        db.session.flush()
        return validation

    def generate_unsigned_xml(self, draft: NfeDraft) -> NfeXmlVersion:
        draft = self.get_nfe_draft_or_404(draft.id)

        if not draft.access_key:
            self.generate_access_key_for_draft(draft.id)
            db.session.flush()
            draft = self.get_nfe_draft_or_404(draft.id)

        validation = self.validate_draft(draft)
        if not validation.is_valid:
            raise ValueError("Rascunho fiscal inválido. Corrija os erros antes de gerar XML.")

        xml_content = self.xml_builder.build(
            draft.fiscal_payload,
            access_key=draft.access_key,
        )
        latest_version = (
            NfeXmlVersion.query.filter(
                NfeXmlVersion.nfe_draft_id == draft.id,
                NfeXmlVersion.xml_type == NfeXmlType.UNSIGNED.value,
            )
            .order_by(NfeXmlVersion.version_number.desc())
            .first()
        )
        version_number = (latest_version.version_number if latest_version else 0) + 1
        generated_at = datetime.utcnow()
        row = NfeXmlVersion(
            nfe_draft_id=draft.id,
            version_number=version_number,
            xml_type=NfeXmlType.UNSIGNED.value,
            xml_content=xml_content,
            xsd_valid=None,
            xsd_errors=None,
            access_key=draft.access_key,
            generated_by_user_id=self.user_id,
            generated_at=generated_at,
        )
        db.session.add(row)

        draft.status = NfeDraftStatus.XML_GENERATED.value
        draft.updated_at = generated_at

        process = self.import_process_query_for_current_user().filter(ImportProcess.id == draft.import_process_id).first()
        if process:
            process.status = ImportProcessStatus.XML_GENERATED.value
            process.updated_at = datetime.utcnow()

        db.session.flush()
        return row

    def validate_xml_version(
        self,
        draft: NfeDraft,
        xml_version: NfeXmlVersion,
    ) -> NfeXsdValidationResult:
        if xml_version.nfe_draft_id != draft.id:
            raise ValueError("Versão XML não pertence ao rascunho informado.")

        xml_type = getattr(xml_version.xml_type, "value", xml_version.xml_type)
        xml_type = str(xml_type).lower()
        if xml_type not in {
            NfeXmlType.UNSIGNED.value,
            NfeXmlType.SIGNED.value,
        }:
            raise ValueError(
                "A validação XSD desta etapa suporta somente XML de NF-e "
                "não assinado ou assinado."
            )

        result = self.xsd_validator.validate(
            xml_version.xml_content,
            allow_unsigned=xml_type == NfeXmlType.UNSIGNED.value,
        )
        xml_version.xsd_valid = result.is_valid
        xml_version.xsd_errors = result.errors

        latest_version = (
            NfeXmlVersion.query.filter(
                NfeXmlVersion.nfe_draft_id == draft.id,
                NfeXmlVersion.xml_type == xml_type,
            )
            .order_by(NfeXmlVersion.version_number.desc())
            .first()
        )
        if latest_version and latest_version.id == xml_version.id:
            process = (
                self.import_process_query_for_current_user()
                .filter(ImportProcess.id == draft.import_process_id)
                .first()
            )
            if process:
                process.status = (
                    ImportProcessStatus.XML_VALIDATED.value
                    if result.is_valid
                    else ImportProcessStatus.XML_VALIDATION_FAILED.value
                )
                process.updated_at = datetime.utcnow()

        db.session.flush()
        return result

    def sign_xml_version(
        self,
        draft: NfeDraft,
        xml_version: NfeXmlVersion,
        *,
        certificate_id=None,
    ) -> dict[str, Any]:
        if xml_version.nfe_draft_id != draft.id:
            raise ValueError("Versão XML não pertence ao rascunho informado.")
        xml_type = getattr(xml_version.xml_type, "value", xml_version.xml_type)
        if str(xml_type).lower() != NfeXmlType.UNSIGNED.value:
            raise ValueError(
                "Somente uma versão XML não assinada pode ser assinada."
            )
        if xml_version.xsd_valid is not True:
            raise ValueError(
                "O XML não assinado deve ser aprovado no XSD antes da assinatura."
            )
        latest_unsigned = (
            NfeXmlVersion.query.filter(
                NfeXmlVersion.nfe_draft_id == draft.id,
                NfeXmlVersion.xml_type == NfeXmlType.UNSIGNED.value,
            )
            .order_by(NfeXmlVersion.version_number.desc())
            .first()
        )
        if not latest_unsigned or latest_unsigned.id != xml_version.id:
            raise ValueError(
                "Somente a versão XML não assinada mais recente pode ser assinada."
            )

        self.ensure_authorization_ready(draft)

        existing_signed = NfeXmlVersion.query.filter(
            NfeXmlVersion.nfe_draft_id == draft.id,
            NfeXmlVersion.xml_type == NfeXmlType.SIGNED.value,
            NfeXmlVersion.version_number == xml_version.version_number,
        ).first()
        existing_issuance = NfeIssuance.query.filter(
            NfeIssuance.organization_id == draft.organization_id,
            NfeIssuance.nfe_draft_id == draft.id,
        ).first()
        if existing_signed:
            if not existing_issuance or existing_issuance.status != "signed":
                raise ValueError(
                    "Já existe XML assinado, mas o estado da emissão está inconsistente."
                )
            if (
                certificate_id is not None
                and existing_issuance.certificate_id != certificate_id
            ):
                raise ValueError(
                    "O XML já foi assinado com outro certificado."
                )
            certificate = self.certificate_registry.get(
                existing_issuance.certificate_id,
                client_id=draft.importer_id,
            )
            return {
                "xml_version": existing_signed,
                "certificate": certificate,
                "issuance": existing_issuance,
                "replayed": True,
            }

        if (
            draft.updated_at
            and xml_version.generated_at
            and draft.updated_at > xml_version.generated_at
        ):
            raise ValueError(
                "O XML está desatualizado em relação ao rascunho. "
                "Gere e valide uma nova versão antes de assinar."
            )

        environment = getattr(draft.environment, "value", draft.environment)
        certificate = self.certificate_registry.active_for(
            client_id=draft.importer_id,
            environment=str(environment),
            certificate_id=certificate_id,
        )
        issuance = self._issuance_for_signature(
            draft=draft,
            xml_version=xml_version,
            certificate=certificate,
        )

        attempt = self._start_signature_attempt(
            issuance=issuance,
            xml_version=xml_version,
        )
        try:
            provider = getattr(certificate.provider, "value", certificate.provider)
            material = self.certificate_vault.resolve(
                provider=str(provider),
                certificate_ref=certificate.certificate_ref,
                password_ref=certificate.password_ref,
            )
            signature_result = self.xml_signer.sign(
                xml_version.xml_content,
                material=material,
                expected_cnpj=certificate.issuer_cnpj,
            )
            xsd_result = self.xsd_validator.validate(
                signature_result.signed_xml,
                allow_unsigned=False,
            )
            if not xsd_result.is_valid:
                raise NfeXmlSignatureError(
                    "O XML assinado não foi aprovado no XSD oficial."
                )
        except (
            FiscalCertificateError,
            NfeXmlSignatureError,
            NfeXsdConfigurationError,
        ) as exc:
            now = datetime.utcnow()
            attempt.status = NfeAttemptStatus.FAILED.value
            attempt.error_code = type(exc).__name__
            attempt.error_message = str(exc)
            attempt.finished_at = now
            issuance.last_error_code = type(exc).__name__
            issuance.last_error_message = str(exc)
            issuance.updated_at = now
            db.session.flush()
            raise

        self.certificate_registry.apply_loaded_metadata(
            certificate,
            signature_result.certificate,
        )
        now = datetime.utcnow()
        signed_version = NfeXmlVersion(
            nfe_draft_id=draft.id,
            version_number=xml_version.version_number,
            xml_type=NfeXmlType.SIGNED.value,
            xml_content=signature_result.signed_xml,
            xsd_valid=True,
            xsd_errors=[],
            access_key=draft.access_key,
            generated_by_user_id=self.user_id,
            generated_at=now,
        )
        db.session.add(signed_version)
        db.session.flush()

        previous_status = issuance.status
        issuance.certificate_id = certificate.id
        issuance.status = "signed"
        issuance.last_error_code = None
        issuance.last_error_message = None
        issuance.updated_at = now

        attempt.status = NfeAttemptStatus.SUCCEEDED.value
        attempt.response_checksum = signature_result.signed_checksum_sha256
        attempt.response_code = "signed"
        attempt.response_message = "XML assinado e validado no XSD oficial."
        attempt.finished_at = now

        event = NfeIssuanceEvent(
            nfe_issuance_id=issuance.id,
            previous_status=str(
                getattr(previous_status, "value", previous_status)
            ),
            current_status="signed",
            reason="Assinatura XMLDSig concluída.",
            event_metadata={
                "unsigned_xml_version_id": str(xml_version.id),
                "signed_xml_version_id": str(signed_version.id),
                "certificate_fingerprint_sha256": (
                    certificate.certificate_fingerprint_sha256
                ),
                "unsigned_checksum_sha256": (
                    signature_result.unsigned_checksum_sha256
                ),
                "signed_checksum_sha256": (
                    signature_result.signed_checksum_sha256
                ),
            },
            actor_user_id=self.user_id,
            created_at=now,
        )
        db.session.add(event)

        draft.status = NfeDraftStatus.SIGNED.value
        draft.updated_at = now
        process = (
            self.import_process_query_for_current_user()
            .filter(ImportProcess.id == draft.import_process_id)
            .first()
        )
        if process:
            process.status = ImportProcessStatus.XML_SIGNED.value
            process.updated_at = now

        db.session.flush()
        return {
            "xml_version": signed_version,
            "certificate": certificate,
            "issuance": issuance,
            "replayed": False,
        }

    def _issuance_for_signature(
        self,
        *,
        draft: NfeDraft,
        xml_version: NfeXmlVersion,
        certificate,
    ) -> NfeIssuance:
        payload = {
            "draft_id": str(draft.id),
            "unsigned_xml_version_id": str(xml_version.id),
            "unsigned_checksum_sha256": hashlib.sha256(
                xml_version.xml_content.encode("utf-8")
            ).hexdigest(),
            "certificate_id": str(certificate.id),
        }
        idempotency_key = (
            f"sign:{draft.id}:{xml_version.id}:{certificate.id}"
        )
        request_hash = NfeIdempotency.request_hash(payload)
        issuance = NfeIssuance.query.filter(
            NfeIssuance.organization_id == draft.organization_id,
            NfeIssuance.nfe_draft_id == draft.id,
        ).first()
        if issuance:
            if issuance.request_hash != request_hash:
                raise ValueError(
                    "A emissão existente está vinculada a outro XML ou certificado."
                )
            return issuance

        now = datetime.utcnow()
        issuance = NfeIssuance(
            organization_id=draft.organization_id,
            import_process_id=draft.import_process_id,
            nfe_draft_id=draft.id,
            importer_id=draft.importer_id,
            certificate_id=certificate.id,
            environment=draft.environment,
            status="xsd_validated",
            model=getattr(draft.model, "value", draft.model),
            series=str(draft.series).zfill(3),
            number=draft.number,
            access_key=draft.access_key,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_by_user_id=self.user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(issuance)
        db.session.flush()
        return issuance

    def _start_signature_attempt(
        self,
        *,
        issuance: NfeIssuance,
        xml_version: NfeXmlVersion,
    ) -> NfeIssuanceAttempt:
        latest_attempt = (
            NfeIssuanceAttempt.query.filter(
                NfeIssuanceAttempt.nfe_issuance_id == issuance.id,
                NfeIssuanceAttempt.operation
                == NfeAttemptOperation.SIGNATURE.value,
            )
            .order_by(NfeIssuanceAttempt.attempt_number.desc())
            .first()
        )
        attempt = NfeIssuanceAttempt(
            nfe_issuance_id=issuance.id,
            attempt_number=(
                latest_attempt.attempt_number + 1 if latest_attempt else 1
            ),
            operation=NfeAttemptOperation.SIGNATURE.value,
            status=NfeAttemptStatus.STARTED.value,
            request_checksum=hashlib.sha256(
                xml_version.xml_content.encode("utf-8")
            ).hexdigest(),
            started_at=datetime.utcnow(),
        )
        db.session.add(attempt)
        db.session.flush()
        return attempt

    # ------------------------------------------------------------------
    # Normalization / mapping / validation
    # ------------------------------------------------------------------
    def normalize_duimp_payload(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        return self.duimp_normalizer.normalize(raw_payload)

    def map_duimp_to_nfe_payload(
        self,
        *,
        duimp: dict[str, Any],
        process: ImportProcess,
        fiscal_profile: ClientFiscalProfile,
        environment: str,
        series: str,
        number: int | None,
        import_purpose: str,
        tax_configuration: dict[str, Any],
        additional_costs: dict[str, Any] | None = None,
        foreign_supplier: dict[str, Any] | None = None,
        duimp_overrides: dict[str, Any] | None = None,
        document_options: dict[str, Any] | None = None,
        item_defaults: dict[str, Any] | None = None,
        transport: dict[str, Any] | None = None,
        payment: dict[str, Any] | None = None,
        additional_info: dict[str, Any] | None = None,
        item_classifications: dict[str, NfeItemClassification] | None = None,
        preallocated_costs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        resolved_additional_costs = self.resolve_additional_costs(
            duimp=duimp,
            additional_costs=additional_costs,
        )
        items = self.map_duimp_items_to_nfe_items(
            duimp=duimp,
            import_purpose=import_purpose,
            item_classifications=item_classifications,
        )
        item_defaults = item_defaults or {}
        for item in items:
            if item_defaults.get("commercial_unit"):
                item["commercial_unit"] = item_defaults["commercial_unit"]
            if item_defaults.get("taxable_unit"):
                item["taxable_unit"] = item_defaults["taxable_unit"]
        if item_classifications:
            item_costs = (
                [
                    preallocated_costs.get(
                        str(item.get("duimp_item_number")),
                        {},
                    )
                    for item in items
                ]
                if preallocated_costs is not None
                else None
            )
            items, totals = self._calculate_items_with_tax_rules(
                items,
                item_classifications=item_classifications,
                fallback_configuration=tax_configuration,
                additional_costs=resolved_additional_costs,
                preallocated_costs=item_costs,
            )
        else:
            items, totals = self.tax_calculator.calculate(
                items,
                configuration=tax_configuration,
                additional_costs=resolved_additional_costs,
            )
        expected_tax_totals = deepcopy(duimp.get("tax_totals") or {})
        icms_reference = (
            (duimp.get("automation_fiscal_references") or {}).get("icms") or {}
        )
        if icms_reference.get("declared_value") not in (None, ""):
            expected_tax_totals["icms"] = {
                "value": icms_reference["declared_value"]
            }
        reconciliation = self.tax_calculator.reconcile(
            items,
            totals,
            expected_tax_totals=expected_tax_totals,
            expected_additional_costs=resolved_additional_costs,
        )
        issuer = self.build_fiscal_party_from_profile(fiscal_profile)
        recipient = self.build_foreign_party_from_duimp(
            duimp,
            override=foreign_supplier,
        )
        duimp_data = {
            "number": duimp["number"],
            "api_number": duimp.get("api_number"),
            "version": duimp.get("version"),
            "registration_date": duimp.get("registration_date"),
            "clearance_location": duimp.get("clearance_location"),
            "clearance_state": duimp.get("clearance_state"),
            "clearance_date": duimp.get("clearance_date"),
            "transport_mode_code": duimp.get("transport_mode_code"),
            "afrmm_value": duimp.get("afrmm_value", "0"),
            "intermediation_type": duimp.get("intermediation_type") or "1",
            "exporter_code": duimp.get("exporter_code"),
            "import_modality": duimp.get("import_modality"),
            "third_party_tax_id": duimp.get("third_party_tax_id"),
        }
        duimp_data.update(duimp_overrides or {})

        authorization = self._authorization_metadata(items)
        document_options = document_options or {}
        item_purposes = sorted(
            {
                str(item.get("import_purpose") or import_purpose)
                for item in items
            }
        )
        mixed_import_purposes = len(item_purposes) > 1
        resolved_transport = self._transport_with_automatic_weight(
            transport,
            items,
        )
        resolved_additional_info = self._build_import_additional_info(
            duimp=duimp,
            totals=totals,
            additional_costs=resolved_additional_costs,
            options=additional_info,
        )

        return {
            "document": {
                "model": NfeModel.NFE.value,
                "purpose": NfePurpose.NORMAL.value,
                "operation_type": NfeOperationType.ENTRY.value,
                "environment": environment,
                "series": series,
                "number": number,
                "operation_nature": (
                    "Importação de mercadorias"
                    if mixed_import_purposes
                    else document_options.get("operation_nature")
                    or self._default_operation_nature(item_purposes[0])
                ),
                "presence_indicator": document_options.get(
                    "presence_indicator"
                )
                or "9",
                "intermediary_indicator": document_options.get(
                    "intermediary_indicator"
                )
                or "0",
                "import_purpose": import_purpose,
                "item_purposes": item_purposes,
                "mixed_import_purposes": mixed_import_purposes,
                "import_modality": duimp.get("import_modality"),
                "currency": "BRL",
            },
            "import_process": {
                "id": str(process.id),
                "reference_code": process.reference_code,
            },
            "duimp": duimp_data,
            "issuer": issuer,
            "recipient": recipient,
            "items": items,
            "totals": totals,
            "additional_costs": resolved_additional_costs,
            "reconciliation": reconciliation,
            "authorization": authorization,
            "transport": resolved_transport,
            "payment": payment or {"method": "90", "value": "0.00"},
            "additional_info": resolved_additional_info,
            "source": {
                "import_process_id": str(process.id),
                "duimp_source": "DUIMP",
                "fiscal_profile_id": str(fiscal_profile.id),
            },
        }

    def resolve_additional_costs(
        self,
        *,
        duimp: dict[str, Any],
        additional_costs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        resolved = dict(additional_costs or {})
        defaults = {
            "afrmm": duimp.get("afrmm_value") or "0",
            "siscomex_fee": self._duimp_siscomex_fee(duimp),
            "thc": "0",
            "other": "0",
        }
        for name, default in defaults.items():
            if resolved.get(name) in (None, ""):
                resolved[name] = default
        return resolved

    @staticmethod
    def _default_operation_nature(import_purpose: str) -> str:
        return {
            ImportPurpose.RESALE.value: "Compra para comercialização",
            ImportPurpose.INDUSTRIALIZATION.value: (
                "Compra para industrialização"
            ),
            ImportPurpose.FIXED_ASSET.value: (
                "Importação de ativo imobilizado"
            ),
            ImportPurpose.USE_CONSUMPTION.value: (
                "Importação para uso ou consumo"
            ),
            "service_use": "Importação para prestação de serviço",
        }.get(import_purpose, "Importação de mercadoria")

    def _transport_with_automatic_weight(
        self,
        transport: dict[str, Any] | None,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        resolved = deepcopy(transport or {})
        resolved.setdefault("freight_mode", "9")
        net_weight = sum(
            (self._decimal(item.get("net_weight")) for item in items),
            Decimal("0"),
        )
        volume = deepcopy(resolved.get("volume") or {})
        if net_weight > 0 and volume.get("net_weight") in (None, ""):
            volume["net_weight"] = str(net_weight)
            volume["net_weight_source"] = "duimp_items"
        if volume:
            resolved["volume"] = volume
        return resolved

    def _build_import_additional_info(
        self,
        *,
        duimp: dict[str, Any],
        totals: dict[str, Any],
        additional_costs: dict[str, Any],
        options: dict[str, Any] | None,
    ) -> dict[str, Any]:
        options = deepcopy(options or {})
        parts = []
        if options.get("automatic_summary", True):
            registration_date = self._date_value(duimp.get("registration_date"))
            formatted_date = (
                registration_date.strftime("%d.%m.%Y")
                if registration_date
                else "não informada"
            )
            parts.append(
                "Conforme DUIMP: "
                f"{duimp.get('api_number') or duimp.get('number')}. "
                f"Registrada em: {formatted_date}."
            )
            values = (
                ("II", totals.get("ii_value")),
                ("TAXA SISCOMEX", additional_costs.get("siscomex_fee")),
                ("FRETE INTERNACIONAL", totals.get("freight_value")),
                ("THC", additional_costs.get("thc")),
                ("AFRMM", additional_costs.get("afrmm")),
                ("SEGURO INTERNACIONAL", totals.get("insurance_value")),
                ("COFINS", totals.get("cofins_value")),
                ("PIS", totals.get("pis_value")),
            )
            parts.append(
                "; ".join(
                    f"{label}: R$ {self._format_brl(value)}"
                    for label, value in values
                )
                + "."
            )
        for key in ("complementary", "legal_text"):
            text = str(options.get(key) or "").strip()
            if text:
                parts.append(text)
        return {
            "fiscal": options.get("fiscal"),
            "complementary": " ".join(parts) or None,
            "automatic_summary": options.get("automatic_summary", True),
            "legal_text_present": bool(
                str(options.get("legal_text") or "").strip()
            ),
        }

    def _format_brl(self, value: Any) -> str:
        formatted = f"{self._decimal(value):,.2f}"
        return formatted.replace(",", "_").replace(".", ",").replace(
            "_", "."
        )

    @staticmethod
    def _duimp_siscomex_fee(duimp: dict[str, Any]) -> Any:
        tax_totals = duimp.get("tax_totals") or {}
        for name in (
            "taxa_utilizacao",
            "taxa_utilizacao_siscomex",
            "taxa_siscomex",
        ):
            tax = tax_totals.get(name)
            if isinstance(tax, dict):
                value = tax.get("value")
            else:
                value = tax
            if value not in (None, ""):
                return value
        return "0"

    def build_fiscal_party_from_profile(self, profile: ClientFiscalProfile) -> dict[str, Any]:
        return {
            "client_id": str(profile.client_id),
            "fiscal_profile_id": str(profile.id),
            "cnpj": normalize_cnpj(profile.cnpj),
            "legal_name": profile.legal_name,
            "trade_name": profile.trade_name,
            "state_registration": self._digits(profile.state_registration),
            "tax_regime": profile.tax_regime,
            "address": {
                "street": profile.street,
                "number": profile.number,
                "complement": profile.complement,
                "district": profile.district,
                "city_code": self._digits(profile.city_code),
                "city_name": profile.city_name,
                "state": profile.state,
                "zip_code": self._digits(profile.zip_code),
                "country_code": profile.country_code or "1058",
                "country_name": profile.country_name or "Brasil",
            },
            "contact": {
                "phone": self._digits(profile.phone),
                "email": profile.email,
            },
        }

    def build_foreign_party_from_duimp(
        self,
        duimp: dict[str, Any],
        *,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        supplier = dict(duimp.get("foreign_supplier") or {})
        supplier.update(override or {})
        address = supplier.get("address") or {}
        if isinstance(address, str):
            address = {"street": address}

        country = supplier.get("country") or {}
        return {
            "party_type": "foreign",
            "foreign_id": supplier.get("foreign_id")
            or supplier.get("foreign_tax_id"),
            "legal_name": supplier.get("legal_name") or supplier.get("name"),
            "ind_ie_dest": "9",
            "address": {
                "street": address.get("street")
                or address.get("logradouro")
                or "EXTERIOR",
                "number": address.get("number") or address.get("numero") or "0",
                "complement": address.get("complement")
                or address.get("complemento"),
                "district": address.get("district")
                or address.get("bairro")
                or "EXTERIOR",
                "city_code": "9999999",
                "city_name": address.get("city_name")
                or address.get("municipio")
                or "EXTERIOR",
                "state": "EX",
                "zip_code": None,
                "country_code": supplier.get("country_code")
                or country.get("code")
                or country.get("codigo"),
                "country_name": supplier.get("country_name")
                or supplier.get("country_name_pt")
                or country.get("name")
                or country.get("descricao"),
                "country_iso_alpha_2": supplier.get("country_iso_alpha_2"),
            },
        }

    def map_duimp_items_to_nfe_items(
        self,
        *,
        duimp: dict[str, Any],
        import_purpose: str,
        item_classifications: dict[str, NfeItemClassification] | None = None,
    ) -> list[dict[str, Any]]:
        mapped_items = []
        item_classifications = item_classifications or {}

        for index, item in enumerate(duimp.get("items", []), start=1):
            classification = item_classifications.get(str(item["number"]))
            item_purpose = (
                classification.import_purpose
                if classification
                else import_purpose
            )
            cfop = (
                classification.cfop
                if classification and classification.cfop
                else self._resolve_cfop(item_purpose)
            )
            product_value = self._decimal(item.get("product_value"))
            quantity = self._decimal(item.get("quantity"))
            unit_value = self._decimal(item.get("unit_value"))
            if unit_value == 0 and quantity > 0:
                unit_value = product_value / quantity

            manufacturer_fallback = bool(
                item.get("manufacturer_code_missing_from_portal")
                and not item.get("manufacturer_code")
            )
            manufacturer_code = (
                "0000" if manufacturer_fallback else item.get("manufacturer_code")
            )
            item_additional_info = item.get("additional_info")
            tax_rule_snapshot = None
            if classification and classification.tax_rule:
                tax_rule_snapshot = deepcopy(
                    classification.tax_rule_snapshot
                    or self._tax_rule_snapshot(
                        classification.tax_rule,
                        ncm=self._digits(item.get("ncm")),
                    )
                )
            mapped_items.append(
                {
                    "item_number": index,
                    "duimp_item_number": item["number"],
                    "product_code": item["product_code"],
                    "description": item["description"],
                    "additional_info": item_additional_info,
                    "ncm": item["ncm"],
                    "cfop": cfop,
                    "import_purpose": item_purpose,
                    "tax_rule_id": (
                        str(classification.tax_rule_id)
                        if classification and classification.tax_rule_id
                        else None
                    ),
                    "tax_rule_snapshot": tax_rule_snapshot,
                    "item_classification_id": (
                        str(classification.id)
                        if classification and classification.id
                        else None
                    ),
                    "cest": item.get("cest"),
                    "commercial_unit": item["commercial_unit"],
                    "commercial_quantity": str(quantity),
                    "commercial_unit_value": str(unit_value),
                    "taxable_unit": item["taxable_unit"],
                    "taxable_quantity": item["taxable_quantity"],
                    "taxable_unit_value": item["taxable_unit_value"],
                    "product_value": str(product_value),
                    "customs_value": item.get("customs_value")
                    or str(product_value),
                    "net_weight": item.get("net_weight", "0"),
                    "freight_value": item.get("freight_value", "0"),
                    "insurance_value": item.get("insurance_value", "0"),
                    "discount_value": item.get("discount_value", "0"),
                    "other_value": item.get("other_value", "0"),
                    "import_payload": {
                        "duimp_number": duimp["number"],
                        "duimp_version": duimp.get("version"),
                        "registration_date": duimp.get("registration_date"),
                        "duimp_item_number": item["number"],
                        "addition_number": item.get("addition_number"),
                        "sequence_number": item.get("sequence_number"),
                        "manufacturer_code": manufacturer_code,
                        "manufacturer_code_source": (
                            "fallback_missing_portal_code"
                            if manufacturer_fallback
                            else "portal_unico"
                        ),
                        "manufacturer_code_warning": (
                            "Portal Único retornou fabricante sem código; aplicado "
                            "fallback controlado 0000."
                            if manufacturer_fallback
                            else None
                        ),
                        "additional_info": item_additional_info,
                        "exporter_code": item.get("exporter_code") or duimp.get("exporter_code"),
                        "drawback_number": item.get("drawback_number"),
                    },
                    "tax_payload": item.get("taxes") or {},
                    "tax_classification_code": item.get("tax_classification_code"),
                    "raw_source_payload": item.get("raw"),
                }
            )
        return mapped_items

    def _calculate_items_with_tax_rules(
        self,
        items: list[dict[str, Any]],
        *,
        item_classifications: dict[str, NfeItemClassification],
        fallback_configuration: dict[str, Any],
        additional_costs: dict[str, Any],
        preallocated_costs: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rule_ids = {
            str(row.tax_rule_id)
            for row in item_classifications.values()
            if row.tax_rule_id
        }
        if len(rule_ids) == 1:
            only_classification = next(iter(item_classifications.values()))
            return self.tax_calculator.calculate(
                items,
                configuration=self._classification_tax_configuration(
                    only_classification,
                    fallback_configuration,
                ),
                additional_costs=additional_costs,
                preallocated_costs=preallocated_costs,
            )

        # O primeiro cálculo realiza um único rateio global das despesas.
        # Em seguida cada item é recalculado com sua própria regra, sem repetir
        # AFRMM, Siscomex, THC ou outras despesas.
        allocated_items, _ = self.tax_calculator.calculate(
            items,
            configuration=fallback_configuration,
            additional_costs=additional_costs,
            preallocated_costs=preallocated_costs,
        )
        calculated_items = []
        for item in allocated_items:
            item_number = str(item.get("duimp_item_number"))
            classification = item_classifications.get(
                item_number
            )
            configuration = (
                self._classification_tax_configuration(
                    classification,
                    fallback_configuration,
                )
            )
            source_item = next(
                source
                for source in items
                if str(source.get("duimp_item_number")) == item_number
            )
            item_costs = deepcopy(item.get("cost_allocation") or {})
            calculated, _ = self.tax_calculator.calculate(
                [source_item],
                configuration=configuration,
                additional_costs=item_costs,
                preallocated_costs=[item_costs],
            )
            calculated_items.append(calculated[0])
        return (
            calculated_items,
            self.tax_calculator.calculate_totals(calculated_items),
        )

    @staticmethod
    def _classification_tax_configuration(
        classification: NfeItemClassification | None,
        fallback_configuration: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = (
            classification.tax_rule_snapshot
            if classification
            else None
        ) or {}
        if snapshot.get("configuration_json") is not None:
            return deepcopy(snapshot["configuration_json"])
        if classification and classification.tax_rule:
            return deepcopy(classification.tax_rule.configuration_json or {})
        return deepcopy(fallback_configuration)

    def calculate_nfe_totals(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return self.tax_calculator.calculate_totals(items)

    @staticmethod
    def _authorization_metadata(items: list[dict[str, Any]]) -> dict[str, Any]:
        blockers = []
        diagnostic_treatments = {
            str(icms.get("cst") or "").zfill(2)
            for item in items
            if (
                icms := ((item.get("tax_payload") or {}).get("icms") or {})
            ).get("diagnostic_only")
        }
        if "51" in diagnostic_treatments:
            blockers.append(
                {
                    "code": "missing_nominal_icms_rate",
                    "field": "tax_configuration.icms_rate",
                    "message": (
                        "A transmissão está bloqueada até a equipe fiscal confirmar "
                        "a alíquota nominal do ICMS e o enquadramento do TTD."
                    ),
                }
            )
        if diagnostic_treatments & {"40", "41", "50"}:
            blockers.append(
                {
                    "code": "unconfirmed_icms_tax_treatment",
                    "field": "tax_configuration.icms_cst",
                    "message": (
                        "A assinatura e a transmissão estão bloqueadas até a "
                        "equipe fiscal confirmar se a exoneração integral deve "
                        "usar ICMS CST 40, 41 ou 50."
                    ),
                }
            )
        pending_homologation = diagnostic_treatments & {
            "10",
            "20",
            "30",
            "60",
            "70",
        }
        if pending_homologation:
            blockers.append(
                {
                    "code": "unconfirmed_icms_cst_calculation",
                    "field": "tax_configuration.icms_tax_treatment_confirmed",
                    "message": (
                        "A assinatura e a transmissão estão bloqueadas até a "
                        "equipe fiscal homologar o cálculo dos CSTs de ICMS: "
                        + ", ".join(sorted(pending_homologation))
                        + "."
                    ),
                }
            )
        return {
            "ready": not blockers,
            "blockers": blockers,
            "mode": "diagnostic" if diagnostic_treatments else "fiscal",
        }

    def ensure_authorization_ready(self, draft: NfeDraft) -> None:
        """Guarda obrigatória do futuro envio à SEFAZ."""
        self._refresh_draft_payload_from_items(draft)
        authorization = (draft.fiscal_payload or {}).get("authorization") or {}
        if not authorization.get("ready"):
            codes = ", ".join(
                str(blocker.get("code"))
                for blocker in authorization.get("blockers") or []
            )
            raise ValueError(
                "Assinatura/transmissão da NF-e bloqueada por pendências fiscais"
                + (f": {codes}." if codes else ".")
            )

    def validate_nfe_payload(self, payload: dict[str, Any]) -> ValidationResult:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        document = payload.get("document") or {}
        if document.get("model") != NfeModel.NFE.value:
            errors.append({"field": "document.model", "message": "Modelo da NF-e deve ser 55."})
        if document.get("operation_type") != NfeOperationType.ENTRY.value:
            errors.append({"field": "document.operation_type", "message": "NF-e de importação deve ser operação de entrada."})
        if document.get("environment") not in FiscalEnvironment.values():
            errors.append({"field": "document.environment", "message": "Ambiente fiscal inválido."})
        if not document.get("series"):
            errors.append({"field": "document.series", "message": "Série da NF-e é obrigatória."})

        duimp = payload.get("duimp") or {}
        if not duimp.get("number"):
            errors.append({"field": "duimp.number", "message": "Número da DUIMP é obrigatório."})
        for field, message in {
            "registration_date": "Data de registro da DUIMP é obrigatória.",
            "clearance_location": "Local de desembaraço é obrigatório.",
            "clearance_state": "UF de desembaraço é obrigatória.",
            "clearance_date": "Data de desembaraço é obrigatória.",
            "transport_mode_code": "Via de transporte é obrigatória.",
        }.items():
            if not duimp.get(field):
                errors.append({"field": f"duimp.{field}", "message": message})
        transport_mode_code = duimp.get("transport_mode_code")
        if (
            transport_mode_code
            and str(transport_mode_code).strip() not in NFE_TRANSPORT_MODE_CODES
        ):
            errors.append(
                {
                    "field": "duimp.transport_mode_code",
                    "message": "Via de transporte inválida. Selecione um código de 1 a 13.",
                }
            )
        if duimp.get("intermediation_type") in {"2", "3"}:
            if len(self._digits(duimp.get("third_party_tax_id"))) not in {11, 14}:
                errors.append(
                    {
                        "field": "duimp.third_party_tax_id",
                        "message": "CPF/CNPJ do adquirente ou encomendante é obrigatório.",
                    }
                )
            if len(str(duimp.get("third_party_state") or "")) != 2:
                errors.append(
                    {
                        "field": "duimp.third_party_state",
                        "message": "UF do adquirente ou encomendante é obrigatória.",
                    }
                )

        self._validate_fiscal_party(payload.get("issuer") or {}, errors, "issuer")
        self._validate_recipient(payload.get("recipient") or {}, errors)

        items = payload.get("items") or []
        if not items:
            errors.append({"field": "items", "message": "A NF-e precisa ter ao menos um item."})

        for index, item in enumerate(items, start=1):
            prefix = f"items[{index}]"
            description = str(item.get("description") or "").strip()
            if not description:
                errors.append({"field": f"{prefix}.description", "message": "Descrição do item é obrigatória."})
            elif description.casefold() == "mercadoria importada":
                errors.append(
                    {
                        "field": f"{prefix}.description",
                        "message": (
                            "Produto não enriquecido pelo Catálogo de Produtos."
                        ),
                    }
                )
            if len(self._digits(item.get("ncm"))) != 8:
                errors.append({"field": f"{prefix}.ncm", "message": "NCM deve conter 8 dígitos."})
            if not str(item.get("cfop", "")).startswith("3"):
                errors.append({"field": f"{prefix}.cfop", "message": "CFOP de importação deve iniciar com 3."})
            if self._decimal(item.get("commercial_quantity")) <= 0:
                errors.append({"field": f"{prefix}.commercial_quantity", "message": "Quantidade comercial deve ser maior que zero."})
            if self._decimal(item.get("product_value")) <= 0:
                errors.append({"field": f"{prefix}.product_value", "message": "Valor do produto deve ser maior que zero."})
            if not item.get("import_payload"):
                errors.append({"field": f"{prefix}.import_payload", "message": "Dados de importação são obrigatórios."})
            taxes = item.get("tax_payload") or {}
            missing_taxes = [
                tax for tax in ("icms", "ipi", "ii", "pis", "cofins")
                if not taxes.get(tax)
            ]
            if missing_taxes:
                errors.append(
                    {
                        "field": f"{prefix}.tax_payload",
                        "message": "Tributos obrigatórios ausentes: " + ", ".join(missing_taxes) + ".",
                    }
                )
            import_payload = item.get("import_payload") or {}
            if import_payload.get("manufacturer_code_source") == (
                "fallback_missing_portal_code"
            ):
                warnings.append(
                    {
                        "field": f"{prefix}.import_payload.manufacturer_code",
                        "message": import_payload.get("manufacturer_code_warning"),
                    }
                )

        authorization = payload.get("authorization") or {}
        for blocker in authorization.get("blockers") or []:
            warnings.append(
                {
                    "field": blocker.get("field") or "authorization",
                    "code": blocker.get("code"),
                    "message": blocker.get("message"),
                }
            )

        transport = payload.get("transport") or {}
        freight_mode = str(transport.get("freight_mode") or "9")
        if freight_mode != "9" and not transport.get("carrier"):
            warnings.append(
                {
                    "field": "transport.carrier",
                    "code": "missing_transport_carrier",
                    "message": (
                        "A modalidade de frete indica transporte contratado, "
                        "mas a transportadora ainda não foi informada."
                    ),
                }
            )
        volume = transport.get("volume") or {}
        missing_volume_fields = [
            field
            for field in ("quantity", "species", "gross_weight")
            if volume.get(field) in (None, "")
        ]
        if missing_volume_fields:
            warnings.append(
                {
                    "field": "transport.volume",
                    "code": "incomplete_transport_volume",
                    "missing_fields": missing_volume_fields,
                    "message": (
                        "Complete os volumes da carga antes da emissão final: "
                        + ", ".join(missing_volume_fields)
                        + ". O peso líquido, quando disponível, é calculado "
                        "automaticamente pelos itens da DUIMP."
                    ),
                }
            )

        has_icms_benefit = any(
            bool(
                item.get("benefit_code")
                or (((item.get("tax_payload") or {}).get("icms") or {}).get(
                    "benefit_code"
                ))
            )
            for item in items
        )
        additional_info = payload.get("additional_info") or {}
        if has_icms_benefit and not additional_info.get("legal_text_present"):
            warnings.append(
                {
                    "field": "additional_info.legal_text",
                    "code": "missing_fiscal_legal_text",
                    "message": (
                        "Há benefício fiscal de ICMS nos itens, mas o texto "
                        "legal/TTD ainda não foi configurado para as "
                        "informações complementares."
                    ),
                }
            )

        reconciliation = payload.get("reconciliation") or {}
        if reconciliation.get("status") == "requires_review":
            failed_checks = [
                check
                for check in reconciliation.get("checks") or []
                if not check.get("matches")
            ]
            diagnostic_icms = (
                self.tax_calculator.is_diagnostic_full_icms_reduction(items)
            )
            blocking_checks = []
            warning_checks = []
            for check in failed_checks:
                is_controlled_diagnostic_icms = (
                    check.get("name") == "duimp_icms" and diagnostic_icms
                )
                if (
                    check.get("blocking", True) is False
                    or is_controlled_diagnostic_icms
                ):
                    warning_checks.append(check)
                else:
                    blocking_checks.append(check)

            if blocking_checks:
                failed_names = ", ".join(
                    str(check.get("name")) for check in blocking_checks
                )
                errors.append(
                    {
                        "field": "reconciliation",
                        "message": (
                            "A reconciliação fiscal encontrou divergências"
                            + (f": {failed_names}." if failed_names else ".")
                        ),
                    }
                )

            for check in warning_checks:
                warnings.append(
                    {
                        "field": f"reconciliation.{check.get('name')}",
                        "code": check.get("code")
                        or "diagnostic_icms_reconciliation_difference",
                        "message": check.get("message")
                        or (
                            "O ICMS declarado na DUIMP diverge do vICMS da "
                            "NF-e diagnóstica com CST 51 e redução integral da "
                            "base. A divergência exige revisão fiscal, mas não "
                            "impede a geração do XML não assinado."
                        ),
                    }
                )

        warnings.append(
            {
                "field": "tax_rules",
                "message": "Revisar a parametrização tributária e as regras estaduais antes da autorização da NF-e.",
            }
        )
        return ValidationResult(errors=errors, warnings=warnings)

    def _validate_fiscal_party(self, party: dict[str, Any], errors: list[dict[str, Any]], prefix: str) -> None:
        if len(self._digits(party.get("cnpj"))) != 14:
            errors.append({"field": f"{prefix}.cnpj", "message": "CNPJ deve conter 14 dígitos."})
        if not party.get("legal_name"):
            errors.append({"field": f"{prefix}.legal_name", "message": "Razão social é obrigatória."})
        if party.get("tax_regime") not in {"1", "2", "3"}:
            errors.append({"field": f"{prefix}.tax_regime", "message": "Regime tributário deve ser 1, 2 ou 3."})

        address = party.get("address") or {}
        required_fields = {
            "street": "Logradouro é obrigatório.",
            "number": "Número do endereço é obrigatório.",
            "district": "Bairro é obrigatório.",
            "city_code": "Código IBGE do município é obrigatório.",
            "city_name": "Município é obrigatório.",
            "state": "UF é obrigatória.",
            "zip_code": "CEP é obrigatório.",
        }
        for field, message in required_fields.items():
            if not address.get(field):
                errors.append({"field": f"{prefix}.address.{field}", "message": message})

        if address.get("city_code") and len(self._digits(address.get("city_code"))) != 7:
            errors.append({"field": f"{prefix}.address.city_code", "message": "Código IBGE deve conter 7 dígitos."})
        if address.get("zip_code") and len(self._digits(address.get("zip_code"))) != 8:
            errors.append({"field": f"{prefix}.address.zip_code", "message": "CEP deve conter 8 dígitos."})
        if address.get("state") and len(str(address.get("state"))) != 2:
            errors.append({"field": f"{prefix}.address.state", "message": "UF deve conter 2 caracteres."})

    def _validate_recipient(
        self, party: dict[str, Any], errors: list[dict[str, Any]]
    ) -> None:
        if party.get("party_type") != "foreign":
            self._validate_fiscal_party(party, errors, "recipient")
            return

        if not party.get("legal_name"):
            errors.append(
                {
                    "field": "recipient.legal_name",
                    "message": "Nome do fornecedor estrangeiro é obrigatório.",
                }
            )
        address = party.get("address") or {}
        for field, message in {
            "street": "Endereço do fornecedor estrangeiro é obrigatório.",
            "number": "Número do endereço estrangeiro é obrigatório.",
            "district": "Bairro/distrito estrangeiro é obrigatório.",
            "city_name": "Cidade estrangeira é obrigatória.",
            "country_code": "Código BACEN do país é obrigatório.",
            "country_name": "Nome do país é obrigatório.",
        }.items():
            if not address.get(field):
                errors.append({"field": f"recipient.address.{field}", "message": message})
        if address.get("state") != "EX":
            errors.append(
                {
                    "field": "recipient.address.state",
                    "message": "UF de destinatário estrangeiro deve ser EX.",
                }
            )
        if address.get("city_code") != "9999999":
            errors.append(
                {
                    "field": "recipient.address.city_code",
                    "message": "Código de município do exterior deve ser 9999999.",
                }
            )

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------
    def _create_duimp_snapshot(
        self,
        *,
        process: ImportProcess,
        duimp_number: str,
        duimp_version: str | None,
        raw_payload: dict[str, Any],
        normalized_payload: dict[str, Any],
        source_provider: str,
    ) -> DuimpSnapshot:
        now = datetime.utcnow()
        snapshot = DuimpSnapshot(
            organization_id=self._require_organization_id(),
            import_process_id=process.id,
            duimp_number=duimp_number,
            duimp_version=duimp_version,
            raw_payload=raw_payload,
            normalized_payload=normalized_payload,
            source_provider=source_provider,
            fetched_at=now,
            checksum=self._checksum(raw_payload),
            created_at=now,
        )
        db.session.add(snapshot)
        db.session.flush()
        return snapshot

    def _build_draft_item(self, *, draft: NfeDraft, item_payload: dict[str, Any]) -> NfeDraftItem:
        now = datetime.utcnow()
        import_payload = deepcopy(item_payload.get("import_payload") or {})
        import_payload["cost_allocation"] = deepcopy(
            item_payload.get("cost_allocation") or {}
        )
        tax_payload = deepcopy(item_payload.get("tax_payload") or {})
        tax_payload.setdefault("icms", {}).setdefault(
            "calculation_source",
            "tax_rule" if item_payload.get("tax_rule_id") else "request_configuration",
        )
        return NfeDraftItem(
            nfe_draft_id=draft.id,
            item_number=item_payload["item_number"],
            duimp_item_number=item_payload.get("duimp_item_number"),
            product_code=item_payload["product_code"],
            description=item_payload["description"],
            ncm=item_payload["ncm"],
            cfop=item_payload["cfop"],
            cest=item_payload.get("cest"),
            commercial_unit=item_payload["commercial_unit"],
            commercial_quantity=self._decimal(item_payload["commercial_quantity"]),
            commercial_unit_value=self._decimal(item_payload["commercial_unit_value"]),
            taxable_unit=item_payload["taxable_unit"],
            taxable_quantity=self._decimal(item_payload["taxable_quantity"]),
            taxable_unit_value=self._decimal(item_payload["taxable_unit_value"]),
            product_value=self._decimal(item_payload["product_value"]),
            freight_value=self._decimal(item_payload.get("freight_value", 0)),
            insurance_value=self._decimal(item_payload.get("insurance_value", 0)),
            discount_value=self._decimal(item_payload.get("discount_value", 0)),
            other_value=self._decimal(item_payload.get("other_value", 0)),
            import_payload=import_payload,
            tax_payload=tax_payload,
            import_purpose=item_payload.get("import_purpose"),
            tax_rule_id=(
                UUID(str(item_payload["tax_rule_id"]))
                if item_payload.get("tax_rule_id")
                else None
            ),
            tax_rule_snapshot=deepcopy(
                item_payload.get("tax_rule_snapshot")
            ),
            item_classification_id=(
                UUID(str(item_payload["item_classification_id"]))
                if item_payload.get("item_classification_id")
                else None
            ),
            raw_source_payload=item_payload.get("raw_source_payload"),
            created_at=now,
            updated_at=now,
        )

    def _refresh_draft_payload_from_items(self, draft: NfeDraft) -> None:
        rows = (
            NfeDraftItem.query
            .filter(NfeDraftItem.nfe_draft_id == draft.id)
            .order_by(NfeDraftItem.item_number.asc())
            .all()
        )
        payload = dict(draft.fiscal_payload or {})
        payload["items"] = [self._item_to_payload(row) for row in rows]
        payload["totals"] = self.calculate_nfe_totals(payload["items"])
        expected_taxes = {}
        for check in (payload.get("reconciliation") or {}).get("checks") or []:
            name = str(check.get("name") or "")
            if name.startswith("duimp_"):
                expected_taxes[name.removeprefix("duimp_")] = {
                    "value": check.get("expected")
                }
        payload["reconciliation"] = self.tax_calculator.reconcile(
            payload["items"],
            payload["totals"],
            expected_tax_totals=expected_taxes,
            expected_additional_costs=payload.get("additional_costs") or {},
        )
        payload["authorization"] = self._authorization_metadata(payload["items"])
        draft.fiscal_payload = payload

    def _recalculate_draft_rows(
        self,
        rows: list[NfeDraftItem],
        *,
        fiscal_payload: dict[str, Any],
        additional_costs: dict[str, Any],
    ) -> None:
        current_items = {
            str(item.get("item_number")): item
            for item in fiscal_payload.get("items") or []
        }
        source_items = []
        for row in rows:
            source = self._item_to_payload(row)
            current = current_items.get(str(row.item_number), {})
            old_allocation = deepcopy(
                current.get("cost_allocation")
                or (row.import_payload or {}).get("cost_allocation")
                or {}
            )
            base_other = self._decimal(source.get("other_value")) - sum(
                (self._decimal(value) for value in old_allocation.values()),
                Decimal("0"),
            )
            source["other_value"] = format(
                base_other.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                ".2f",
            )
            source["customs_value"] = (
                current.get("customs_value") or source.get("product_value")
            )
            source["net_weight"] = current.get("net_weight") or "0"
            source_items.append(source)

        costs = {
            name: self._decimal(additional_costs.get(name)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            for name in ("afrmm", "siscomex_fee", "thc", "other")
        }
        customs_weights = [
            self._decimal(item.get("customs_value") or item.get("product_value"))
            for item in source_items
        ]
        net_weights = [self._decimal(item.get("net_weight")) for item in source_items]
        afrmm_weights = (
            net_weights
            if all(weight > 0 for weight in net_weights)
            else customs_weights
        )
        allocations = {
            "afrmm": self.tax_calculator.allocate(costs["afrmm"], afrmm_weights),
            "siscomex_fee": self.tax_calculator.allocate(costs["siscomex_fee"], customs_weights),
            "thc": self.tax_calculator.allocate(costs["thc"], customs_weights),
            "other": self.tax_calculator.allocate(costs["other"], customs_weights),
        }

        for index, (row, source) in enumerate(zip(rows, source_items)):
            item_costs = {
                name: values[index] for name, values in allocations.items()
            }
            current_icms = deepcopy((row.tax_payload or {}).get("icms") or {})
            configuration = (
                deepcopy(row.tax_rule.configuration_json or {})
                if row.tax_rule
                else self._tax_configuration_from_item(row)
            )
            calculated, _ = self.tax_calculator.calculate(
                [source],
                configuration=configuration,
                additional_costs=item_costs,
                preallocated_costs=[item_costs],
            )
            result = calculated[0]
            result_import = deepcopy(result.get("import_payload") or {})
            result_import["cost_allocation"] = {
                name: format(value, ".2f") for name, value in item_costs.items()
            }
            result["import_payload"] = result_import
            if current_icms.get("calculation_source") == "manual_adjustment":
                manual = (current_icms.get("manual_adjustment") or {}).get("values") or {}
                manual_icms = self._manual_icms_values(
                    current_icms,
                    manual,
                )
                manual_icms["adjustment_history"] = deepcopy(
                    current_icms.get("adjustment_history") or []
                )
                manual_icms["manual_adjustment"] = deepcopy(
                    current_icms.get("manual_adjustment")
                )
                result.setdefault("tax_payload", {})["icms"] = manual_icms

            row.other_value = self._decimal(result.get("other_value"))
            row.import_payload = result_import
            row.tax_payload = result.get("tax_payload") or {}
            row.updated_at = datetime.utcnow()

        fiscal_payload["items"] = [self._item_to_payload(row) for row in rows]
        fiscal_payload["totals"] = self.calculate_nfe_totals(fiscal_payload["items"])

    @staticmethod
    def _tax_configuration_from_item(item: NfeDraftItem) -> dict[str, Any]:
        taxes = item.tax_payload or {}
        icms = taxes.get("icms") or {}
        ipi = taxes.get("ipi") or {}
        pis = taxes.get("pis") or {}
        cofins = taxes.get("cofins") or {}
        return {
            "icms_origin": icms.get("origin") or "1",
            "icms_cst": icms.get("cst") or "90",
            "icms_base_method": icms.get("base_method") or "3",
            "icms_rate": icms.get("rate"),
            "icms_base_reduction_rate": icms.get("base_reduction_rate"),
            "icms_deferment_rate": icms.get("deferment_rate"),
            "icms_st_base_method": icms.get("st_base_method"),
            "icms_st_mva_rate": icms.get("st_mva_rate"),
            "icms_st_base_reduction_rate": icms.get(
                "st_base_reduction_rate"
            ),
            "icms_st_rate": icms.get("st_rate"),
            "icms_st_retained_base": icms.get("retained_st_base"),
            "icms_st_retained_rate": icms.get("retained_st_rate"),
            "icms_st_retained_value": icms.get("retained_st_value"),
            "icms_tax_treatment_confirmed": icms.get("tax_treatment_confirmed"),
            "ipi_cst": ipi.get("cst") or "49",
            "ipi_enquiry_code": ipi.get("enquiry_code") or "999",
            "pis_cst": pis.get("cst") or "98",
            "cofins_cst": cofins.get("cst") or "98",
        }

    def _item_to_payload(self, item: NfeDraftItem) -> dict[str, Any]:
        import_payload = deepcopy(item.import_payload or {})
        cost_allocation = deepcopy(import_payload.pop("cost_allocation", {}) or {})
        return {
            "item_number": item.item_number,
            "duimp_item_number": item.duimp_item_number,
            "product_code": item.product_code,
            "description": item.description,
            "ncm": item.ncm,
            "cfop": item.cfop,
            "import_purpose": item.import_purpose,
            "tax_rule_id": str(item.tax_rule_id) if item.tax_rule_id else None,
            "tax_rule_snapshot": deepcopy(item.tax_rule_snapshot),
            "item_classification_id": (
                str(item.item_classification_id)
                if item.item_classification_id
                else None
            ),
            "cest": item.cest,
            "benefit_code": (
                ((item.tax_payload or {}).get("icms") or {}).get(
                    "benefit_code"
                )
            ),
            "commercial_unit": item.commercial_unit,
            "commercial_quantity": str(item.commercial_quantity),
            "commercial_unit_value": str(item.commercial_unit_value),
            "taxable_unit": item.taxable_unit,
            "taxable_quantity": str(item.taxable_quantity),
            "taxable_unit_value": str(item.taxable_unit_value),
            "product_value": str(item.product_value),
            "freight_value": str(item.freight_value),
            "insurance_value": str(item.insurance_value),
            "discount_value": str(item.discount_value),
            "other_value": str(item.other_value),
            "additional_info": import_payload.get("additional_info"),
            "cost_allocation": cost_allocation,
            "import_payload": import_payload,
            "tax_payload": item.tax_payload,
            "raw_source_payload": item.raw_source_payload,
        }

    def _log_external_request(
        self,
        *,
        process: ImportProcess,
        provider: str,
        endpoint_name: str,
        method: str,
        request_payload: dict[str, Any] | None,
        response_payload: Any,
        success: bool,
        status_code: int | None,
        started_at: datetime,
        finished_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ExternalApiRequestLog:
        row = ExternalApiRequestLog(
            organization_id=self._require_organization_id(),
            import_process_id=process.id,
            provider=provider,
            endpoint_name=endpoint_name,
            method=method,
            request_payload=request_payload,
            response_payload=response_payload,
            status_code=status_code,
            success=success,
            error_code=error_code,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def _resolve_cfop(self, import_purpose: str) -> str:
        mapping = {
            ImportPurpose.RESALE.value: "3102",
            ImportPurpose.INDUSTRIALIZATION.value: "3101",
            ImportPurpose.FIXED_ASSET.value: "3127",
            ImportPurpose.USE_CONSUMPTION.value: "3556",
            # caso você adicione esse enum depois:
            "service_use": "3126",
        }
        if import_purpose not in mapping:
            raise ValueError("Finalidade de importação inválida para definição do CFOP.")
        return mapping[import_purpose]

    @staticmethod
    def _merge_defaults(
        defaults: dict[str, Any] | None,
        explicit: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = deepcopy(defaults or {})
        for key, value in (explicit or {}).items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = ImportNfeService._merge_defaults(
                    result[key], value
                )
            else:
                result[key] = deepcopy(value)
        return result

    @staticmethod
    def _json_compatible(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {
                key: ImportNfeService._json_compatible(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                ImportNfeService._json_compatible(item) for item in value
            ]
        return value

    @staticmethod
    def _date_value(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @staticmethod
    def _tax_rule_to_dict(rule: ClientImportTaxRule) -> dict[str, Any]:
        return {
            "id": str(rule.id),
            "client_id": str(rule.client_id),
            "name": rule.name,
            "issuer_state": rule.issuer_state,
            "import_purpose": rule.import_purpose,
            "import_modality": rule.import_modality,
            "tax_regime": rule.tax_regime,
            "ncm_pattern": rule.ncm_pattern,
            "ncm_scope_type": ImportNfeService._rule_ncm_scope_type(rule),
            "ncm_patterns": ImportNfeService._rule_ncm_patterns(rule),
            "priority": rule.priority,
            "revision": rule.revision or 1,
            "configuration_json": rule.configuration_json,
            "additional_cost_defaults": rule.additional_cost_defaults,
            "transport_defaults": rule.transport_defaults,
            "payment_defaults": rule.payment_defaults,
            "active": rule.active,
            "effective_from": (
                rule.effective_from.isoformat() if rule.effective_from else None
            ),
            "effective_until": (
                rule.effective_until.isoformat() if rule.effective_until else None
            ),
        }

    @classmethod
    def _tax_rule_snapshot(
        cls,
        rule: ClientImportTaxRule,
        *,
        ncm: str,
        selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = cls._tax_rule_to_dict(rule)
        snapshot["tax_rule_id"] = snapshot.pop("id")
        snapshot["applied_ncm"] = cls._digits(ncm)
        snapshot["selection"] = deepcopy(selection or {})
        return cls._json_compatible(snapshot)

    def _require_organization_id(self):
        if not self.organization_id:
            raise ValueError("Usuário atual não possui organization_id.")
        return self.organization_id

    @staticmethod
    def _checksum(payload: dict[str, Any]) -> str:
        content = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value or "0"))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    @staticmethod
    def _digits(value: Any) -> str:
        return "".join(ch for ch in str(value or "") if ch.isdigit())

    @staticmethod
    def _empty_to_none(value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def _enum_value(value: Any) -> Any:
        return getattr(value, "value", value)

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() + "Z" if value else None
