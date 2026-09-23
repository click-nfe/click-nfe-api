from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from app.extensions import db
from app.integrations.portal_unico import (
    PortalCredentialStore,
    PortalUnicoApiError,
    PortalUnicoCredentials,
    PortalUnicoDuimpGateway,
    PortalUnicoIntegrationError,
)
from app.models import (
    ExternalAuthType,
    ExternalConnectionStatus,
    ExternalProvider,
    ExternalProviderConnection,
    FiscalEnvironment,
)


class PortalUnicoSettingsError(ValueError):
    """Erro seguro e apresentável na configuração organizacional."""


class PortalUnicoHealthcheckError(PortalUnicoSettingsError):
    def __init__(self, message: str, *, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class PortalCredentialRotation:
    connection: ExternalProviderConnection
    new_reference: str
    previous_reference: str | None


GatewayFactory = Callable[
    [PortalUnicoCredentials, str, dict[str, Any]],
    Any,
]


class PortalUnicoSettingsService:
    PROVIDER = ExternalProvider.PORTAL_UNICO.value
    ENVIRONMENT = FiscalEnvironment.PRODUCTION.value
    AUTH_TYPE = ExternalAuthType.API_KEY.value
    ROLE_TYPE = "IMPEXP"

    def __init__(
        self,
        *,
        current_user: Any,
        credential_store: PortalCredentialStore,
        credential_resolver: Any,
        gateway_factory: GatewayFactory | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self.current_user = current_user
        self.organization_id = getattr(current_user, "organization_id", None)
        self.credential_store = credential_store
        self.credential_resolver = credential_resolver
        self.gateway_factory = gateway_factory or self._default_gateway
        self.timeout_seconds = timeout_seconds

    def status(self) -> dict[str, Any]:
        return self.public_data(self._connection())

    def configure(
        self,
        *,
        client_id: str,
        client_secret: str,
        role_type: str,
    ) -> PortalCredentialRotation:
        client_id = str(client_id or "").strip()
        client_secret = str(client_secret or "").strip()
        credentials = PortalUnicoCredentials(
            client_id=client_id,
            client_secret=client_secret,
            role_type=role_type,
        )
        new_reference = self.credential_store.store(
            organization_id=str(self._require_organization_id()),
            credentials=credentials,
        )
        try:
            now = datetime.utcnow()
            connection = self._connection(lock=True)
            previous_reference = (
                connection.credentials_ref if connection else None
            )
            if connection is None:
                connection = ExternalProviderConnection(
                    organization_id=self.organization_id,
                    importer_id=None,
                    provider=self.PROVIDER,
                    environment=self.ENVIRONMENT,
                    created_at=now,
                )
                db.session.add(connection)

            existing_config = dict(connection.config_json or {})
            connection.auth_type = self.AUTH_TYPE
            connection.status = ExternalConnectionStatus.ACTIVE.value
            connection.credentials_ref = new_reference
            connection.config_json = {
                **existing_config,
                "role_type": self.ROLE_TYPE,
                "client_id_hint": self._client_id_hint(client_id),
                "credential_storage_provider": self.credential_store.provider,
            }
            connection.last_healthcheck_at = None
            connection.last_error = None
            connection.updated_at = now
            db.session.flush()
            return PortalCredentialRotation(
                connection=connection,
                new_reference=new_reference,
                previous_reference=previous_reference,
            )
        except Exception:
            self.credential_store.delete(new_reference)
            raise

    def test_connection(self) -> dict[str, Any]:
        connection = self._connection(lock=True)
        if connection is None or not connection.credentials_ref:
            raise PortalUnicoSettingsError(
                "Configure o Client-Id e o Client-Secret antes de testar a conexão."
            )

        config = dict(connection.config_json or {})
        role_type = config.get("role_type") or self.ROLE_TYPE
        now = datetime.utcnow()
        try:
            credentials = self.credential_resolver.resolve(
                connection.credentials_ref,
                role_type=role_type,
            )
            gateway = self.gateway_factory(
                credentials,
                self.ENVIRONMENT,
                config,
            )
            gateway.healthcheck()
        except (PortalUnicoApiError, PortalUnicoIntegrationError) as exc:
            message = self._healthcheck_error(exc)
            connection.status = ExternalConnectionStatus.ERROR.value
            connection.last_healthcheck_at = now
            connection.last_error = message
            connection.updated_at = now
            db.session.flush()
            result = self.public_data(connection)
            raise PortalUnicoHealthcheckError(
                message,
                result=result,
            ) from exc

        connection.status = ExternalConnectionStatus.ACTIVE.value
        connection.last_healthcheck_at = now
        connection.last_error = None
        connection.updated_at = now
        db.session.flush()
        return self.public_data(connection)

    def cleanup_reference(self, reference: str | None) -> None:
        if reference and reference.startswith("local:"):
            self.credential_store.delete(reference)

    def public_data(
        self,
        connection: ExternalProviderConnection | None,
    ) -> dict[str, Any]:
        if connection is None or not connection.credentials_ref:
            return {
                "connection_id": None,
                "provider": self.PROVIDER,
                "environment": self.ENVIRONMENT,
                "configured": False,
                "state": "not_configured",
                "ready_for_duimp": False,
                "role_type": self.ROLE_TYPE,
                "client_id_hint": None,
                "credential_storage_provider": None,
                "last_healthcheck_at": None,
                "last_error": None,
                "blockers": [
                    {
                        "code": "portal_unico_not_configured",
                        "message": "Configure as credenciais do Portal Único.",
                    }
                ],
            }

        status = self._enum_value(connection.status)
        config = dict(connection.config_json or {})
        if status == ExternalConnectionStatus.ERROR.value:
            state = "error"
        elif status == ExternalConnectionStatus.INACTIVE.value:
            state = "inactive"
        elif connection.last_healthcheck_at:
            state = "connected"
        else:
            state = "pending_test"

        blockers = []
        if state == "pending_test":
            blockers.append(
                {
                    "code": "portal_unico_not_tested",
                    "message": "Teste a conexão com o Portal Único antes de consultar uma DUIMP.",
                }
            )
        elif state == "error":
            blockers.append(
                {
                    "code": "portal_unico_connection_error",
                    "message": connection.last_error
                    or "A conexão com o Portal Único precisa ser validada novamente.",
                }
            )
        elif state == "inactive":
            blockers.append(
                {
                    "code": "portal_unico_connection_inactive",
                    "message": "A conexão com o Portal Único está inativa.",
                }
            )

        return {
            "connection_id": str(connection.id),
            "provider": self.PROVIDER,
            "environment": self.ENVIRONMENT,
            "configured": True,
            "state": state,
            "ready_for_duimp": state == "connected",
            "role_type": config.get("role_type") or self.ROLE_TYPE,
            "client_id_hint": config.get("client_id_hint"),
            "credential_storage_provider": config.get(
                "credential_storage_provider"
            ),
            "last_healthcheck_at": self._iso(
                connection.last_healthcheck_at
            ),
            "last_error": connection.last_error,
            "blockers": blockers,
        }

    def _connection(
        self,
        *,
        lock: bool = False,
    ) -> ExternalProviderConnection | None:
        query = ExternalProviderConnection.query.filter(
            ExternalProviderConnection.organization_id
            == self._require_organization_id(),
            ExternalProviderConnection.importer_id.is_(None),
            ExternalProviderConnection.provider == self.PROVIDER,
            ExternalProviderConnection.environment == self.ENVIRONMENT,
        ).order_by(ExternalProviderConnection.updated_at.desc())
        if lock:
            query = query.with_for_update()
        return query.first()

    def _default_gateway(
        self,
        credentials: PortalUnicoCredentials,
        environment: str,
        config: dict[str, Any],
    ) -> PortalUnicoDuimpGateway:
        return PortalUnicoDuimpGateway(
            credentials=credentials,
            environment=environment,
            base_url=config.get("base_url"),
            timeout_seconds=float(
                config.get("timeout_seconds") or self.timeout_seconds
            ),
        )

    def _require_organization_id(self):
        if not self.organization_id:
            raise PortalUnicoSettingsError(
                "O usuário precisa estar vinculado a uma organização."
            )
        return self.organization_id

    @staticmethod
    def _client_id_hint(client_id: str) -> str:
        visible = client_id[-4:] if len(client_id) >= 4 else client_id
        return f"••••{visible}"

    @staticmethod
    def _healthcheck_error(exc: Exception) -> str:
        if isinstance(exc, PortalUnicoApiError) and exc.status_code in {
            401,
            403,
        }:
            return "O Portal Único rejeitou o Client-Id ou o Client-Secret."
        return "Não foi possível autenticar no Portal Único neste momento."

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value))

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None
