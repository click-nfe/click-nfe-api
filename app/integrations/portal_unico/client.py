from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken


class PortalUnicoIntegrationError(RuntimeError):
    """Falha de configuração, transporte ou contrato do Portal Único."""


class PortalUnicoApiError(PortalUnicoIntegrationError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


@dataclass(frozen=True)
class DuimpIdentifier:
    """Representa o número da DUIMP nas formas usada pela API e pelo operador."""

    compact: str

    _PATTERN = re.compile(r"^[0-9]{2}BR[0-9]{11}$")

    @classmethod
    def parse(cls, value: str) -> "DuimpIdentifier":
        raw = str(value or "").strip().upper()
        compact = re.sub(r"[\s-]", "", raw)
        if not cls._PATTERN.fullmatch(compact):
            raise ValueError(
                "Número da DUIMP inválido. Use o formato 26BR0000000000-1 "
                "ou 26BR00000000001."
            )
        return cls(compact=compact)

    @property
    def formatted(self) -> str:
        return f"{self.compact[:-1]}-{self.compact[-1]}"


@dataclass(frozen=True)
class PortalUnicoCredentials:
    client_id: str
    client_secret: str
    role_type: str = "IMPEXP"

    def __post_init__(self) -> None:
        if not self.client_id or not self.client_secret:
            raise PortalUnicoIntegrationError(
                "Client-Id e Client-Secret do Portal Único são obrigatórios."
            )
        if self.role_type != "IMPEXP":
            raise PortalUnicoIntegrationError(
                "A consulta de DUIMP por interveniente privado requer Role-Type IMPEXP."
            )


class PortalCredentialResolver(Protocol):
    def resolve(self, credentials_ref: str, *, role_type: str) -> PortalUnicoCredentials:
        ...


class PortalCredentialStore(PortalCredentialResolver, Protocol):
    provider: str

    def store(
        self,
        *,
        organization_id: str,
        credentials: PortalUnicoCredentials,
    ) -> str:
        ...

    def delete(self, credentials_ref: str) -> None:
        ...


class LocalEncryptedFilePortalCredentialStore:
    """Cofre local criptografado para as chaves do Portal Único.

    O banco recebe somente uma referência opaca. O conteúdo é persistido com
    Fernet em um volume Docker e pode ser substituído por um writer do Google
    Secret Manager sem alterar as rotas da aplicação.
    """

    provider = "local_encrypted_file"
    _REFERENCE = re.compile(
        r"^local:([0-9a-f-]{36})/([0-9a-f]{32})\.portal\.json\.enc$"
    )

    def __init__(
        self,
        *,
        root_dir: str | Path | None = None,
        encryption_key: str | bytes | None = None,
        secret_key: str | None = None,
    ) -> None:
        self.root_dir = Path(
            root_dir
            or os.getenv(
                "PORTAL_UNICO_LOCAL_SECRET_DIR",
                "/app/data/portal-unico",
            )
        ).resolve()
        self._fernet = Fernet(
            self._key(
                encryption_key
                or os.getenv("PORTAL_UNICO_LOCAL_SECRET_KEY"),
                secret_key=secret_key or os.getenv("SECRET_KEY"),
            )
        )

    def store(
        self,
        *,
        organization_id: str,
        credentials: PortalUnicoCredentials,
    ) -> str:
        organization = self._uuid(organization_id)
        token = uuid.uuid4().hex
        reference = f"local:{organization}/{token}.portal.json.enc"
        payload = json.dumps(
            {
                "version": 1,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._write(reference, payload)
        return reference

    def resolve(
        self,
        credentials_ref: str,
        *,
        role_type: str,
    ) -> PortalUnicoCredentials:
        try:
            encrypted = self._path(credentials_ref).read_bytes()
            payload = json.loads(self._fernet.decrypt(encrypted).decode("utf-8"))
            if payload.get("version") != 1:
                raise ValueError("unsupported payload version")
            return PortalUnicoCredentials(
                client_id=str(payload.get("client_id") or ""),
                client_secret=str(payload.get("client_secret") or ""),
                role_type=role_type,
            )
        except FileNotFoundError as exc:
            raise PortalUnicoIntegrationError(
                "As credenciais locais do Portal Único não foram encontradas."
            ) from exc
        except InvalidToken as exc:
            raise PortalUnicoIntegrationError(
                "As credenciais locais do Portal Único não puderam ser descriptografadas."
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PortalUnicoIntegrationError(
                "As credenciais locais do Portal Único são inválidas."
            ) from exc

    def delete(self, credentials_ref: str) -> None:
        try:
            self._path(credentials_ref).unlink(missing_ok=True)
        except (OSError, PortalUnicoIntegrationError):
            return

    def _write(self, reference: str, content: bytes) -> None:
        path = self._path(reference)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=".credential-",
                delete=False,
            ) as temporary:
                temporary.write(self._fernet.encrypt(content))
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

    def _path(self, reference: str) -> Path:
        match = self._REFERENCE.fullmatch(str(reference or ""))
        if not match:
            raise PortalUnicoIntegrationError(
                "A referência local das credenciais do Portal Único é inválida."
            )
        relative = Path(match.group(1), f"{match.group(2)}.portal.json.enc")
        path = (self.root_dir / relative).resolve()
        if not path.is_relative_to(self.root_dir):
            raise PortalUnicoIntegrationError(
                "A referência local das credenciais do Portal Único é inválida."
            )
        return path

    @staticmethod
    def _key(
        configured: str | bytes | None,
        *,
        secret_key: str | None,
    ) -> bytes:
        if configured:
            value = (
                configured.encode("ascii")
                if isinstance(configured, str)
                else configured
            )
            try:
                Fernet(value)
            except (ValueError, TypeError) as exc:
                raise PortalUnicoIntegrationError(
                    "PORTAL_UNICO_LOCAL_SECRET_KEY não contém uma chave Fernet válida."
                ) from exc
            return value
        if not secret_key:
            raise PortalUnicoIntegrationError(
                "PORTAL_UNICO_LOCAL_SECRET_KEY ou SECRET_KEY é obrigatória para o cofre local."
            )
        digest = hashlib.sha256(
            f"click-nfe-local-portal-unico:{secret_key}".encode("utf-8")
        ).digest()
        return base64.urlsafe_b64encode(digest)

    @staticmethod
    def _uuid(value: str) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise PortalUnicoIntegrationError(
                "O identificador da organização é inválido."
            ) from exc


class EnvironmentPortalCredentialResolver:
    """Resolve credenciais sem armazená-las no banco.

    ``credentials_ref=env:CLIENTE_ACME_PORTAL`` procura as variáveis
    ``CLIENTE_ACME_PORTAL_CLIENT_ID`` e ``CLIENTE_ACME_PORTAL_CLIENT_SECRET``.
    Em produção, este resolver pode ser substituído por Secret Manager/Vault.
    """

    def resolve(self, credentials_ref: str, *, role_type: str) -> PortalUnicoCredentials:
        prefix = str(credentials_ref or "")
        if not prefix.startswith("env:"):
            raise PortalUnicoIntegrationError(
                "credentials_ref inválido. Para o resolver atual use env:NOME_DO_SEGREDO."
            )

        env_prefix = prefix.removeprefix("env:").strip()
        if not env_prefix or not re.fullmatch(r"[A-Z][A-Z0-9_]*", env_prefix):
            raise PortalUnicoIntegrationError(
                "O prefixo de credentials_ref deve conter apenas letras maiúsculas, "
                "números e sublinhado."
            )

        return PortalUnicoCredentials(
            client_id=os.getenv(f"{env_prefix}_CLIENT_ID", ""),
            client_secret=os.getenv(f"{env_prefix}_CLIENT_SECRET", ""),
            role_type=role_type,
        )


class GcpSecretManagerPortalCredentialResolver:
    """Resolve o par de chaves do Portal Único no Secret Manager.

    ``credentials_ref=gcp:PORTAL_UNICO`` acessa os secrets
    ``PORTAL_UNICO_CLIENT_ID`` e ``PORTAL_UNICO_CLIENT_SECRET`` no projeto
    indicado por ``GOOGLE_CLOUD_PROJECT`` ou ``GCP_PROJECT_ID``.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        project_id: str | None = None,
        secret_version: str | None = None,
    ) -> None:
        self._client = client
        self.project_id = (
            project_id
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT_ID")
        )
        self.secret_version = secret_version or os.getenv(
            "PORTAL_UNICO_SECRET_VERSION", "1"
        )

    def resolve(self, credentials_ref: str, *, role_type: str) -> PortalUnicoCredentials:
        prefix = str(credentials_ref or "")
        if not prefix.startswith("gcp:"):
            raise PortalUnicoIntegrationError(
                "credentials_ref inválido. Para o Secret Manager use gcp:NOME."
            )

        secret_prefix = prefix.removeprefix("gcp:").strip()
        if not secret_prefix or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*", secret_prefix
        ):
            raise PortalUnicoIntegrationError(
                "O prefixo dos secrets contém caracteres inválidos."
            )
        if not self.project_id:
            raise PortalUnicoIntegrationError(
                "GOOGLE_CLOUD_PROJECT ou GCP_PROJECT_ID é obrigatório."
            )
        if not re.fullmatch(r"[1-9][0-9]*|latest", self.secret_version):
            raise PortalUnicoIntegrationError(
                "PORTAL_UNICO_SECRET_VERSION deve ser um número ou latest."
            )

        client_id = self._access_secret(f"{secret_prefix}_CLIENT_ID")
        client_secret = self._access_secret(f"{secret_prefix}_CLIENT_SECRET")
        return PortalUnicoCredentials(
            client_id=client_id,
            client_secret=client_secret,
            role_type=role_type,
        )

    def _access_secret(self, secret_id: str) -> str:
        client = self._secret_manager_client()
        resource = (
            f"projects/{self.project_id}/secrets/{secret_id}/versions/"
            f"{self.secret_version}"
        )
        try:
            response = client.access_secret_version(request={"name": resource})
            value = response.payload.data.decode("utf-8").strip()
        except Exception as exc:
            raise PortalUnicoIntegrationError(
                f"Não foi possível acessar o secret {secret_id}."
            ) from exc
        if not value:
            raise PortalUnicoIntegrationError(
                f"O secret {secret_id} está vazio."
            )
        return value

    def _secret_manager_client(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import secretmanager
            except ImportError as exc:
                raise PortalUnicoIntegrationError(
                    "A dependência google-cloud-secret-manager não está instalada."
                ) from exc
            self._client = secretmanager.SecretManagerServiceClient()
        return self._client


class DefaultPortalCredentialResolver:
    """Seleciona o provider a partir do prefixo salvo em credentials_ref."""

    def __init__(
        self,
        *,
        environment_resolver: PortalCredentialResolver | None = None,
        gcp_resolver: PortalCredentialResolver | None = None,
        local_resolver: PortalCredentialResolver | None = None,
    ) -> None:
        self.environment_resolver = (
            environment_resolver or EnvironmentPortalCredentialResolver()
        )
        self.gcp_resolver = (
            gcp_resolver or GcpSecretManagerPortalCredentialResolver()
        )
        self.local_resolver = local_resolver

    def resolve(self, credentials_ref: str, *, role_type: str) -> PortalUnicoCredentials:
        reference = str(credentials_ref or "")
        if reference.startswith("env:"):
            return self.environment_resolver.resolve(reference, role_type=role_type)
        if reference.startswith("gcp:"):
            return self.gcp_resolver.resolve(reference, role_type=role_type)
        if reference.startswith("local:"):
            local_resolver = (
                self.local_resolver
                or LocalEncryptedFilePortalCredentialStore()
            )
            return local_resolver.resolve(reference, role_type=role_type)
        raise PortalUnicoIntegrationError(
            "credentials_ref deve começar com local:, env: ou gcp:."
        )


def portal_credential_store_from_config(
    config: Mapping[str, Any],
) -> PortalCredentialStore:
    configured = config.get("PORTAL_UNICO_CREDENTIAL_STORE")
    if configured is not None:
        return configured
    provider = config.get(
        "PORTAL_UNICO_CREDENTIAL_STORAGE_PROVIDER",
        "local_encrypted_file",
    )
    if provider != "local_encrypted_file":
        raise PortalUnicoIntegrationError(
            "O provider de gravação das credenciais do Portal Único não é suportado."
        )
    return LocalEncryptedFilePortalCredentialStore(
        root_dir=config.get(
            "PORTAL_UNICO_LOCAL_SECRET_DIR",
            "/app/data/portal-unico",
        ),
        encryption_key=config.get("PORTAL_UNICO_LOCAL_SECRET_KEY"),
        secret_key=config.get("SECRET_KEY"),
    )


def portal_credential_resolver_from_config(
    config: Mapping[str, Any],
) -> PortalCredentialResolver:
    configured = config.get("PORTAL_UNICO_CREDENTIAL_RESOLVER")
    if configured is not None:
        return configured
    local_store = LocalEncryptedFilePortalCredentialStore(
        root_dir=config.get(
            "PORTAL_UNICO_LOCAL_SECRET_DIR",
            "/app/data/portal-unico",
        ),
        encryption_key=config.get("PORTAL_UNICO_LOCAL_SECRET_KEY"),
        secret_key=config.get("SECRET_KEY"),
    )
    return DefaultPortalCredentialResolver(local_resolver=local_store)


@dataclass(frozen=True)
class PortalUnicoResponse:
    status_code: int
    headers: Mapping[str, str]
    payload: Any

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


class JsonTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> PortalUnicoResponse:
        ...


class UrllibJsonTransport:
    """Transporte JSON mínimo, sem dependência externa e injetável nos testes."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> PortalUnicoResponse:
        if query:
            url = f"{url}?{urlencode(query)}"

        request_headers = {
            "Accept": "application/json",
            "User-Agent": "triagem-aduaneira-api/1.0",
            **dict(headers or {}),
        }
        data = b"" if method.upper() in {"POST", "PUT", "PATCH"} else None
        request = Request(url, data=data, headers=request_headers, method=method.upper())

        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read()
                payload = self._decode_json(body)
                return PortalUnicoResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    payload=payload,
                )
        except HTTPError as exc:
            payload = self._decode_json(exc.read())
            error_code, message = self._api_error_details(payload)
            raise PortalUnicoApiError(
                message or f"Portal Único retornou HTTP {exc.code}.",
                status_code=exc.code,
                error_code=error_code,
            ) from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise PortalUnicoIntegrationError(
                "Não foi possível conectar ao Portal Único."
            ) from exc

    @staticmethod
    def _decode_json(body: bytes) -> Any:
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PortalUnicoIntegrationError(
                "O Portal Único retornou uma resposta que não é JSON válido."
            ) from exc

    @staticmethod
    def _api_error_details(payload: Any) -> tuple[str | None, str | None]:
        if not isinstance(payload, dict):
            return None, None
        code = payload.get("code") or payload.get("codigo") or payload.get("errorCode")
        message = (
            payload.get("message")
            or payload.get("mensagem")
            or payload.get("detail")
            or payload.get("error")
        )
        return (str(code) if code else None, str(message) if message else None)


class PortalUnicoDuimpGateway:
    AUTH_PATH = "/portal/api/autenticar/chave-acesso"
    DUIMP_BASE_PATH = "/duimp-api/api/ext"
    CATALOG_BASE_PATH = "/catp/api/ext"
    CCT_IMPORT_BASE_PATH = "/ccta/api/ext"
    PCCE_BASE_PATH = "/pcce/api"
    TABX_BASE_PATH = "/tabx/api/ext"
    ENVIRONMENT_HOSTS = {
        "homologation": "https://val.portalunico.siscomex.gov.br",
        "validation": "https://val.portalunico.siscomex.gov.br",
        "production": "https://portalunico.siscomex.gov.br",
    }

    def __init__(
        self,
        *,
        credentials: PortalUnicoCredentials,
        environment: str,
        transport: JsonTransport | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 30,
        page_size: int = 100,
    ) -> None:
        if environment not in self.ENVIRONMENT_HOSTS:
            raise PortalUnicoIntegrationError(
                "Ambiente do Portal Único deve ser validation/homologation ou production."
            )
        if not 1 <= page_size <= 100:
            raise ValueError("page_size deve estar entre 1 e 100.")

        self.credentials = credentials
        self.environment = environment
        self.transport = transport or UrllibJsonTransport()
        self.base_url = (base_url or self.ENVIRONMENT_HOSTS[environment]).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.page_size = page_size
        self._authorization: str | None = None
        self._csrf_token: str | None = None

    def authenticate(self) -> None:
        response = self.transport.request(
            "POST",
            f"{self.base_url}{self.AUTH_PATH}",
            headers={
                "Client-Id": self.credentials.client_id,
                "Client-Secret": self.credentials.client_secret,
                "Role-Type": self.credentials.role_type,
            },
            timeout_seconds=self.timeout_seconds,
        )
        authorization = response.header("Set-Token")
        csrf_token = response.header("X-CSRF-Token")
        if not authorization or not csrf_token:
            raise PortalUnicoIntegrationError(
                "Autenticação concluída sem os headers Set-Token e X-CSRF-Token."
            )
        self._authorization = authorization
        self._csrf_token = csrf_token

    def fetch_duimp(
        self,
        *,
        duimp_number: str,
        duimp_payload: dict[str, Any] | None = None,
        enrich_catalog: bool = True,
    ) -> dict[str, Any]:
        if duimp_payload is not None:
            raise ValueError("O gateway real não aceita duimp_payload manual.")

        identifier = DuimpIdentifier.parse(duimp_number)
        self.authenticate()

        version_payload = self._get(f"/duimp/{identifier.compact}/versoes")
        version = self._extract_version(version_payload)
        general = self._get(f"/duimp/{identifier.compact}/{version}")
        expected_items = int(general.get("quantidadeItens") or 0)
        items = self._fetch_items(identifier.compact, version, expected_items)

        if expected_items and len(items) != expected_items:
            raise PortalUnicoIntegrationError(
                f"A DUIMP informa {expected_items} itens, mas a API retornou {len(items)}."
            )

        catalog_enrichment = self._empty_catalog_enrichment()
        if enrich_catalog:
            importer = (general.get("identificacao") or {}).get("importador") or {}
            catalog_enrichment = self._enrich_catalog(
                items,
                importer_tax_id=importer.get("ni"),
            )

        return {
            "provider": "portal_unico",
            "numero": identifier.formatted,
            "numeroApi": identifier.compact,
            "versao": str(version),
            "dadosGerais": general,
            "itens": items,
            "catalogEnrichment": catalog_enrichment,
        }

    def healthcheck(self) -> dict[str, str]:
        self.authenticate()
        return {"status": "ok", "environment": self.environment}

    def fetch_catalog_product(
        self,
        *,
        cpf_cnpj_root: str,
        product_code: str,
        product_version: str,
    ) -> dict[str, Any]:
        self._ensure_authenticated()
        root = self._tax_id_root(cpf_cnpj_root)
        payload = self._get_api(
            self.CATALOG_BASE_PATH,
            (
                f"/produto/{self._path_segment(root, 'CPF/CNPJ raiz')}"
                f"/{self._path_segment(product_code, 'código do produto')}"
                f"/{self._path_segment(product_version, 'versão do produto')}"
            ),
        )
        return self._catalog_detail(payload, wrapper_keys=("produto",))

    def fetch_foreign_operator(
        self,
        *,
        cpf_cnpj_root: str,
        country_code: str,
        operator_code: str,
        operator_version: str,
    ) -> dict[str, Any]:
        self._ensure_authenticated()
        root = self._tax_id_root(cpf_cnpj_root)
        payload = self._get_api(
            self.CATALOG_BASE_PATH,
            (
                f"/operador-estrangeiro/{self._path_segment(root, 'CPF/CNPJ raiz')}"
                f"/{self._path_segment(country_code, 'país do operador')}"
                f"/{self._path_segment(operator_code, 'código do operador')}"
                f"/{self._path_segment(operator_version, 'versão do operador')}"
            ),
        )
        return self._catalog_detail(
            payload,
            wrapper_keys=("operadorEstrangeiro", "operador"),
        )

    def fetch_cargo_knowledge(
        self,
        *,
        knowledge_number: str,
        responsible_tax_id: str | None = None,
        issue_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Consulta somente-leitura do conhecimento no CCT Importação."""
        self._ensure_authenticated()
        number = str(knowledge_number or "").strip()
        if not number:
            raise ValueError("Número do conhecimento de carga é obrigatório.")

        query: dict[str, Any] = {"numeroConhecimento": number}
        if responsible_tax_id:
            query["cnpjResponsavel"] = self._tax_id(responsible_tax_id)
        if issue_date:
            query["dataEmissao"] = str(issue_date)

        payload = self._get_api(
            self.CCT_IMPORT_BASE_PATH,
            "/conhecimentos",
            query=query,
        )
        if not isinstance(payload, list):
            raise PortalUnicoIntegrationError(
                "A consulta do CCT não retornou uma lista de conhecimentos."
            )
        return [item for item in payload if isinstance(item, dict)]

    def fetch_icms_declaration(self, *, duimp_number: str) -> dict[str, Any]:
        """Consulta somente-leitura da declaração de ICMS no PCCE."""
        self._ensure_authenticated()
        identifier = DuimpIdentifier.parse(duimp_number)
        payload = self._get_api(
            self.PCCE_BASE_PATH,
            f"/ext/priv/icms/{identifier.compact}",
        )
        if not isinstance(payload, dict):
            raise PortalUnicoIntegrationError(
                "A consulta do PCCE não retornou uma declaração de ICMS válida."
            )
        return payload

    def fetch_comex_table(
        self,
        *,
        table_name: str,
        filters: list[dict[str, Any]] | None = None,
        return_fields: list[dict[str, Any]] | None = None,
        sort_fields: list[dict[str, Any]] | None = None,
        level: int = 0,
        offset: int = 1,
    ) -> dict[str, Any]:
        """Consulta uma tabela TABX usando o contrato JSON da API oficial."""
        self._ensure_authenticated()
        name = str(table_name or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{1,50}", name):
            raise ValueError("Nome da tabela TABX inválido.")
        if level not in {0, 1}:
            raise ValueError("Nível da consulta TABX deve ser 0 ou 1.")
        if offset < 1:
            raise ValueError("Offset da consulta TABX deve ser maior ou igual a 1.")

        query: dict[str, Any] = {"nivel": str(level), "offset": str(offset)}
        for key, value in {
            "filtros": filters,
            "camposRetorno": return_fields,
            "camposOrdenacao": sort_fields,
        }.items():
            if value:
                query[key] = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

        payload = self._get_api(
            self.TABX_BASE_PATH,
            f"/tabela/{quote(name, safe='')}",
            query=query,
        )
        if not isinstance(payload, dict):
            raise PortalUnicoIntegrationError(
                "A consulta TABX não retornou um objeto JSON válido."
            )
        return payload

    def _fetch_items(
        self, compact_number: str, version: int, expected_items: int
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        initial = 1

        while True:
            page = self._get(
                f"/duimp/{compact_number}/{version}/itens",
                query={"inicial": initial, "tamanho": self.page_size},
            )
            if not isinstance(page, list):
                raise PortalUnicoIntegrationError(
                    "A consulta de itens da DUIMP não retornou uma lista."
                )
            items.extend(page)

            if not page:
                break
            if expected_items and len(items) >= expected_items:
                break
            if len(page) < self.page_size:
                break
            initial += len(page)

        return items

    def _get(self, path: str, *, query: Mapping[str, Any] | None = None) -> Any:
        return self._get_api(self.DUIMP_BASE_PATH, path, query=query)

    def _get_api(
        self,
        base_path: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        if not self._authorization or not self._csrf_token:
            raise PortalUnicoIntegrationError("Sessão do Portal Único não autenticada.")

        response = self.transport.request(
            "GET",
            f"{self.base_url}{base_path}{path}",
            headers={
                "Authorization": self._authorization,
                "X-CSRF-Token": self._csrf_token,
            },
            query=query,
            timeout_seconds=self.timeout_seconds,
        )
        renewed_csrf = response.header("X-CSRF-Token")
        if renewed_csrf:
            self._csrf_token = renewed_csrf
        return response.payload

    def _ensure_authenticated(self) -> None:
        if not self._authorization or not self._csrf_token:
            self.authenticate()

    def _enrich_catalog(
        self,
        items: list[dict[str, Any]],
        *,
        importer_tax_id: Any,
    ) -> dict[str, Any]:
        summary = self._empty_catalog_enrichment()
        product_cache: dict[tuple[str, str, str], dict[str, Any] | None] = {}
        operator_cache: dict[
            tuple[str, str, str, str], dict[str, Any] | None
        ] = {}

        for item in items:
            product = item.get("produto") or {}
            responsible_tax_id = product.get("niResponsavel") or importer_tax_id
            try:
                root = self._tax_id_root(responsible_tax_id)
            except PortalUnicoIntegrationError as exc:
                root = ""
                summary["failures"].append(
                    {
                        "resource": "product",
                        "code": str(product.get("codigo") or "").strip(),
                        "version": str(product.get("versao") or "").strip(),
                        "message": str(exc),
                    }
                )
            product_code = str(product.get("codigo") or "").strip()
            product_version = str(product.get("versao") or "").strip()
            product_key = (root, product_code, product_version)

            if all(product_key) and product_key not in product_cache:
                summary["products_requested"] += 1
                try:
                    product_cache[product_key] = self.fetch_catalog_product(
                        cpf_cnpj_root=root,
                        product_code=product_code,
                        product_version=product_version,
                    )
                    summary["products_enriched"] += 1
                except PortalUnicoIntegrationError as exc:
                    product_cache[product_key] = None
                    summary["failures"].append(
                        {
                            "resource": "product",
                            "code": product_code,
                            "version": product_version,
                            "message": str(exc),
                        }
                    )

            product_detail = product_cache.get(product_key)
            if product_detail:
                self._merge_catalog_product(product, product_detail)
                item["produto"] = product

            for field in ("exportador", "fabricante"):
                operator = item.get(field) or {}
                country = operator.get("pais") or {}
                if not isinstance(country, dict):
                    country = {}
                try:
                    operator_root = self._tax_id_root(
                        operator.get("niOperador") or root
                    )
                except PortalUnicoIntegrationError as exc:
                    summary["failures"].append(
                        {
                            "resource": "foreign_operator",
                            "code": str(operator.get("codigo") or "").strip(),
                            "version": str(operator.get("versao") or "").strip(),
                            "message": str(exc),
                        }
                    )
                    continue
                country_code = str(country.get("codigo") or "").strip().upper()
                operator_code = str(operator.get("codigo") or "").strip()
                operator_version = str(operator.get("versao") or "").strip()
                operator_key = (
                    operator_root,
                    country_code,
                    operator_code,
                    operator_version,
                )
                if not all(operator_key):
                    continue

                if operator_key not in operator_cache:
                    summary["operators_requested"] += 1
                    try:
                        operator_cache[
                            operator_key
                        ] = self.fetch_foreign_operator(
                            cpf_cnpj_root=operator_root,
                            country_code=country_code,
                            operator_code=operator_code,
                            operator_version=operator_version,
                        )
                        summary["operators_enriched"] += 1
                    except PortalUnicoIntegrationError as exc:
                        operator_cache[operator_key] = None
                        summary["failures"].append(
                            {
                                "resource": "foreign_operator",
                                "country_code": country_code,
                                "code": operator_code,
                                "version": operator_version,
                                "message": str(exc),
                            }
                        )

                operator_detail = operator_cache.get(operator_key)
                if operator_detail:
                    self._merge_catalog_operator(operator, operator_detail)
                    item[field] = operator

        return summary

    @staticmethod
    def _empty_catalog_enrichment() -> dict[str, Any]:
        return {
            "products_requested": 0,
            "products_enriched": 0,
            "operators_requested": 0,
            "operators_enriched": 0,
            "failures": [],
        }

    @classmethod
    def _merge_catalog_product(
        cls,
        product: dict[str, Any],
        detail: dict[str, Any],
    ) -> None:
        product["catalogo"] = detail
        denomination = (
            detail.get("denominacao")
            or detail.get("descricao")
            or detail.get("nome")
        )
        if denomination:
            product["denominacao"] = denomination

        internal_code = cls._catalog_internal_code(detail)
        if internal_code:
            product["codigoInternoNfe"] = internal_code

    @staticmethod
    def _merge_catalog_operator(
        operator: dict[str, Any],
        detail: dict[str, Any],
    ) -> None:
        original_code = operator.get("codigo")
        original_version = operator.get("versao")
        original_country = operator.get("pais")
        original_address = operator.get("endereco")
        operator["catalogo"] = detail

        for key, value in detail.items():
            if value not in (None, "", [], {}):
                operator[key] = value

        operator["codigo"] = original_code or operator.get("codigo")
        operator["versao"] = original_version or operator.get("versao")

        country = (
            dict(original_country)
            if isinstance(original_country, dict)
            else {}
        )
        if detail.get("codigoPais"):
            country["codigo"] = detail["codigoPais"]
        operator["pais"] = country

        address = (
            dict(original_address)
            if isinstance(original_address, dict)
            else {}
        )
        for source_key, target_key in {
            "logradouro": "logradouro",
            "nomeCidade": "city_name",
            "codigoSubdivisaoPais": "subdivision_code",
            "cep": "zip_code",
        }.items():
            if detail.get(source_key):
                address[target_key] = detail[source_key]
        if address:
            operator["endereco"] = address

    @staticmethod
    def _catalog_internal_code(detail: dict[str, Any]) -> str | None:
        direct = (
            detail.get("codigoInterno")
            or detail.get("codigoInternoProduto")
        )
        if direct not in (None, ""):
            return str(direct).strip()

        values = (
            detail.get("codigosInterno")
            or detail.get("codigosInternos")
            or []
        )
        if not isinstance(values, list):
            values = [values]
        for value in values:
            if isinstance(value, dict):
                value = (
                    value.get("codigo")
                    or value.get("valor")
                    or value.get("codigoInterno")
                )
            if value not in (None, ""):
                return str(value).strip()
        return None

    @staticmethod
    def _catalog_detail(
        payload: Any,
        *,
        wrapper_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise PortalUnicoIntegrationError(
                "O Catálogo do Portal Único não retornou um objeto JSON."
            )
        for key in wrapper_keys:
            wrapped = payload.get(key)
            if isinstance(wrapped, dict):
                return wrapped
        return payload

    @staticmethod
    def _tax_id_root(value: Any) -> str:
        digits = "".join(filter(str.isdigit, str(value or "")))
        if len(digits) == 14:
            return digits[:8]
        if len(digits) in {8, 11}:
            return digits
        raise PortalUnicoIntegrationError(
            "CPF/CNPJ responsável pelo Catálogo de Produtos é inválido."
        )

    @staticmethod
    def _tax_id(value: Any) -> str:
        digits = "".join(filter(str.isdigit, str(value or "")))
        if len(digits) in {11, 14}:
            return digits
        raise ValueError("CPF/CNPJ deve conter 11 ou 14 dígitos.")

    @staticmethod
    def _path_segment(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise PortalUnicoIntegrationError(
                f"{label.capitalize()} é obrigatório para consultar o Catálogo."
            )
        return quote(text, safe="")

    @staticmethod
    def _extract_version(payload: Any) -> int:
        if not isinstance(payload, dict) or payload.get("versao") in (None, ""):
            raise PortalUnicoIntegrationError(
                "A consulta de versão vigente não retornou o campo versao."
            )
        try:
            version = int(payload["versao"])
        except (TypeError, ValueError) as exc:
            raise PortalUnicoIntegrationError(
                "A versão vigente retornada pelo Portal Único é inválida."
            ) from exc
        if not 1 <= version <= 9999:
            raise PortalUnicoIntegrationError(
                "A versão vigente retornada pelo Portal Único está fora do intervalo permitido."
            )
        return version
