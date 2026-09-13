from marshmallow import (
    Schema,
    ValidationError,
    fields,
    pre_load,
    validate,
    validates_schema,
)
from marshmallow_sqlalchemy import SQLAlchemyAutoSchema

from app.cnpj import is_valid_cnpj, normalize_cnpj
from app.models import (
    Client,
    ClientContact,
)
from app.models.client import ClientFiscalProfile


class ClientContactSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = ClientContact
        load_instance = True
        include_fk = True


class ClientSchema(SQLAlchemyAutoSchema):
    contatos = fields.Nested(ClientContactSchema, many=True, dump_only=True)
    scope_id = fields.Method("get_scope_id", dump_only=True)
    has_scope = fields.Method("get_has_scope", dump_only=True)

    class Meta:
        model = Client
        load_instance = True
        include_fk = True

    @staticmethod
    def get_scope_id(client):
        return str(client.scope.id) if client.scope else None

    @staticmethod
    def get_has_scope(client):
        return client.scope is not None


class ClientCreateSchema(Schema):
    cnpj = fields.String(required=True)
    razao_social = fields.String(
        required=True,
        validate=validate.Length(min=1, max=255),
    )
    nome_resumido = fields.String(
        allow_none=True,
        load_default=None,
        validate=validate.Length(max=255),
    )
    inscricao_estadual = fields.String(allow_none=True, load_default=None)
    inscricao_municipal = fields.String(allow_none=True, load_default=None)
    endereco_completo_escritorio = fields.String(
        allow_none=True,
        load_default=None,
    )
    endereco_completo_armazem = fields.String(
        allow_none=True,
        load_default=None,
    )
    cnae_principal = fields.String(allow_none=True, load_default=None)
    cnae_secundario = fields.String(allow_none=True, load_default=None)
    regime_tributacao = fields.String(allow_none=True, load_default=None)
    ativo = fields.Boolean(load_default=True)

    @pre_load
    def normalize_payload(self, data, **kwargs):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        if "cnpj" in normalized:
            normalized["cnpj"] = normalize_cnpj(normalized["cnpj"])

        for field_name in (
            "razao_social",
            "nome_resumido",
            "inscricao_estadual",
            "inscricao_municipal",
            "endereco_completo_escritorio",
            "endereco_completo_armazem",
            "cnae_principal",
            "cnae_secundario",
            "regime_tributacao",
        ):
            value = normalized.get(field_name)
            if isinstance(value, str):
                normalized[field_name] = value.strip() or None
        return normalized

    @validates_schema
    def validate_client(self, data, **kwargs):
        errors = {}
        if not data.get("razao_social"):
            errors["razao_social"] = ["Razão social é obrigatória."]
        if not is_valid_cnpj(data.get("cnpj")):
            errors["cnpj"] = [
                "CNPJ inválido. Informe 14 caracteres com dígitos verificadores válidos."
            ]
        if errors:
            raise ValidationError(errors)


class ClientListQuerySchema(Schema):
    q = fields.String(required=False)
    cnpj = fields.String(required=False)
    ativo = fields.Boolean(required=False)
    limit = fields.Integer(load_default=20, validate=validate.Range(min=1, max=200))
    offset = fields.Integer(load_default=0, validate=validate.Range(min=0))

    @pre_load
    def normalize_query(self, data, **kwargs):
        normalized = dict(data)
        if normalized.get("cnpj"):
            normalized["cnpj"] = normalize_cnpj(normalized["cnpj"])
        return normalized


class ClientUpdateSchema(Schema):
    razao_social = fields.String(required=False)
    nome_resumido = fields.String(allow_none=True, required=False)
    inscricao_estadual = fields.String(allow_none=True, required=False)
    inscricao_municipal = fields.String(allow_none=True, required=False)
    endereco_completo_escritorio = fields.String(allow_none=True, required=False)
    endereco_completo_armazem = fields.String(allow_none=True, required=False)
    cnae_principal = fields.String(allow_none=True, required=False)
    cnae_secundario = fields.String(allow_none=True, required=False)
    regime_tributacao = fields.String(allow_none=True, required=False)
    ativo = fields.Boolean(required=False)

class ClientFiscalProfileSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = ClientFiscalProfile
        load_instance = False
        include_fk = True

    id = fields.UUID(dump_only=True)
    organization_id = fields.UUID(dump_only=True)
    client_id = fields.UUID(required=True)

    legal_name = fields.String(required=True)
    trade_name = fields.String(allow_none=True)

    cnpj = fields.String(required=True)
    state_registration = fields.String(allow_none=True)

    tax_regime = fields.String(
        required=True,
        validate=validate.OneOf(["1", "2", "3"]),
    )

    street = fields.String(required=True)
    number = fields.String(required=True)
    complement = fields.String(allow_none=True)
    district = fields.String(required=True)

    city_code = fields.String(required=True, validate=validate.Length(equal=7))
    city_name = fields.String(required=True)
    state = fields.String(required=True, validate=validate.Length(equal=2))
    zip_code = fields.String(required=True, validate=validate.Length(equal=8))

    country_code = fields.String(load_default="1058", dump_default="1058")
    country_name = fields.String(load_default="Brasil", dump_default="Brasil")

    phone = fields.String(allow_none=True)
    email = fields.Email(allow_none=True)
    is_default = fields.Boolean(load_default=True)

    @pre_load
    def normalize_payload(self, data, **kwargs):
        normalized = dict(data)
        if "cnpj" in normalized:
            normalized["cnpj"] = normalize_cnpj(normalized["cnpj"])
        return normalized

    @validates_schema
    def validate_fiscal_profile(self, data, **kwargs):
        cnpj = normalize_cnpj(data.get("cnpj"))
        zip_code = "".join(filter(str.isdigit, str(data.get("zip_code", ""))))
        city_code = "".join(filter(str.isdigit, str(data.get("city_code", ""))))

        errors = {}

        if not is_valid_cnpj(cnpj):
            errors["cnpj"] = [
                "CNPJ inválido. Informe 14 caracteres com dígitos verificadores válidos."
            ]

        if len(zip_code) != 8:
            errors["zip_code"] = ["CEP deve conter 8 dígitos."]

        if len(city_code) != 7:
            errors["city_code"] = ["Código IBGE do município deve conter 7 dígitos."]

        if data.get("state"):
            data["state"] = data["state"].upper()

        if errors:
            raise ValidationError(errors)
