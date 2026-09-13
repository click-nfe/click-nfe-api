from marshmallow import Schema, ValidationError, fields, validate, validates_schema
from marshmallow_sqlalchemy import SQLAlchemyAutoSchema

from app.models import (
    Preposto,
    PrepostoContato,
    PrepostoCredenciado,
    PrepostoLocalidade,
    PrepostoTarifa,
)

class PrepostoContatoSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = PrepostoContato
        load_instance = True
        include_fk = True
        exclude = ("created_at", "updated_at")


class PrepostoLocalidadeSchema(SQLAlchemyAutoSchema):
    tarifas = fields.Nested(lambda: PrepostoTarifaSchema(), many=True, dump_only=True)

    class Meta:
        model = PrepostoLocalidade
        load_instance = True
        include_fk = True
        exclude = ("created_at", "updated_at")


class PrepostoTarifaSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = PrepostoTarifa
        load_instance = True
        include_fk = True
        exclude = ("created_at", "updated_at")


class PrepostoCredenciadoSchema(SQLAlchemyAutoSchema):
    cpf_mascarado = fields.Method("mask_cpf", dump_only=True)

    class Meta:
        model = PrepostoCredenciado
        load_instance = True
        exclude = ("created_at", "updated_at")

    def mask_cpf(self, obj):
        cpf = "".join(character for character in (obj.cpf or "") if character.isdigit())
        if len(cpf) != 11:
            return None
        return f"***.{cpf[3:6]}.{cpf[6:9]}-**"


class PrepostoCredenciadoAdminSchema(SQLAlchemyAutoSchema):
    """Representação completa, restrita às rotas administrativas autenticadas."""

    class Meta:
        model = PrepostoCredenciado
        load_instance = True
        include_fk = True
        exclude = ("created_at", "updated_at")


class PrepostoSchema(SQLAlchemyAutoSchema):
    contatos = fields.Nested(PrepostoContatoSchema, many=True, dump_only=True)
    localidades = fields.Nested(PrepostoLocalidadeSchema, many=True, dump_only=True)
    credenciados = fields.Method("dump_credenciados", dump_only=True)

    class Meta:
        model = Preposto
        load_instance = True
        exclude = ("created_at", "updated_at")

    def dump_credenciados(self, obj):
        credentials = {}
        for link in obj.credenciado_links:
            credential = link.credenciado
            if not link.ativo or not credential.ativo:
                continue
            item = credentials.setdefault(
                str(credential.id),
                {
                    "id": str(credential.id),
                    "nome": credential.nome,
                    "cpf_mascarado": PrepostoCredenciadoSchema().mask_cpf(
                        credential
                    ),
                    "registro_rfb": credential.registro_rfb,
                    "categoria": credential.categoria,
                    "localidade_ids": [],
                },
            )
            item["localidade_ids"].append(str(link.localidade_id))
        return list(credentials.values())


class PrepostoLookupItemSchema(Schema):
    id = fields.String(required=True)
    localidadeId = fields.String(required=True)
    nome = fields.String(required=True)
    cidade = fields.String(required=True)
    uf = fields.String(allow_none=True)
    descricaoLocal = fields.String(allow_none=True)
    operacao = fields.String(required=True)
    valor = fields.Decimal(as_string=False, allow_none=True)
    valorDescricao = fields.String(allow_none=True)
    moeda = fields.String(required=True)
    telefone = fields.String(allow_none=True)
    email = fields.String(allow_none=True)
    contatoNome = fields.String(allow_none=True)
    observacoes = fields.String(allow_none=True)
    tarifas = fields.List(fields.Dict(), required=True)
    credenciados = fields.List(fields.Dict(), required=True)


class PrepostoLookupResponseSchema(Schema):
    items = fields.List(fields.Nested(PrepostoLookupItemSchema), required=True)
    total = fields.Integer(required=True)


class PrepostoCreateSchema(Schema):
    nome = fields.String(required=True)
    razao_social = fields.String(allow_none=True)
    ativo = fields.Boolean(load_default=True)
    observacoes = fields.String(allow_none=True)


class PrepostoUpdateSchema(Schema):
    nome = fields.String(required=False)
    razao_social = fields.String(allow_none=True, required=False)
    ativo = fields.Boolean(required=False)
    observacoes = fields.String(allow_none=True, required=False)


class PrepostoContatoCreateSchema(Schema):
    nome = fields.String(required=True)
    email = fields.Email(allow_none=True)
    telefone = fields.String(allow_none=True)
    whatsapp = fields.String(allow_none=True)
    principal = fields.Boolean(load_default=False)


class PrepostoContatoUpdateSchema(Schema):
    nome = fields.String(required=False)
    email = fields.Email(allow_none=True, required=False)
    telefone = fields.String(allow_none=True, required=False)
    whatsapp = fields.String(allow_none=True, required=False)
    principal = fields.Boolean(required=False)


class PrepostoLocalidadeCreateSchema(Schema):
    cidade = fields.String(required=True)
    uf = fields.String(allow_none=True)
    descricao_local = fields.String(allow_none=True)
    tipo_local = fields.String(
        allow_none=True,
        validate=validate.OneOf(["CIDADE", "PORTO", "AEROPORTO", "CLIA", "FRONTEIRA"]),
    )
    atende_importacao = fields.Boolean(load_default=False)
    atende_exportacao = fields.Boolean(load_default=False)
    valor_importacao = fields.Decimal(as_string=False, allow_none=True)
    valor_exportacao = fields.Decimal(as_string=False, allow_none=True)
    valor_importacao_descricao = fields.String(allow_none=True)
    valor_exportacao_descricao = fields.String(allow_none=True)
    moeda = fields.String(load_default="BRL")
    observacoes = fields.String(allow_none=True)

    @validates_schema
    def validate_operacao(self, data, **kwargs):
        if not data.get("atende_importacao") and not data.get("atende_exportacao"):
            raise ValidationError(
                "A localidade deve atender importação e/ou exportação.",
                field_name="atende_importacao",
            )


class PrepostoLocalidadeUpdateSchema(Schema):
    cidade = fields.String(required=False)
    uf = fields.String(allow_none=True, required=False)
    descricao_local = fields.String(allow_none=True, required=False)
    tipo_local = fields.String(
        allow_none=True,
        required=False,
        validate=validate.OneOf(["CIDADE", "PORTO", "AEROPORTO", "CLIA", "FRONTEIRA"]),
    )
    atende_importacao = fields.Boolean(required=False)
    atende_exportacao = fields.Boolean(required=False)
    valor_importacao = fields.Decimal(as_string=False, allow_none=True, required=False)
    valor_exportacao = fields.Decimal(as_string=False, allow_none=True, required=False)
    valor_importacao_descricao = fields.String(allow_none=True, required=False)
    valor_exportacao_descricao = fields.String(allow_none=True, required=False)
    moeda = fields.String(required=False)
    observacoes = fields.String(allow_none=True, required=False)


class PrepostoTarifaCreateSchema(Schema):
    codigo = fields.String(required=True)
    operacao = fields.String(
        required=True,
        validate=validate.OneOf(["IMPORTACAO", "EXPORTACAO"]),
    )
    tipo = fields.String(required=True)
    valor = fields.Decimal(as_string=False, allow_none=True)
    valor_descricao = fields.String(allow_none=True)
    condicao = fields.String(allow_none=True)
    principal = fields.Boolean(load_default=False)
    moeda = fields.String(load_default="BRL")
    ativo = fields.Boolean(load_default=True)
    observacoes = fields.String(allow_none=True)


class PrepostoTarifaUpdateSchema(Schema):
    codigo = fields.String(required=False)
    operacao = fields.String(
        required=False,
        validate=validate.OneOf(["IMPORTACAO", "EXPORTACAO"]),
    )
    tipo = fields.String(required=False)
    valor = fields.Decimal(as_string=False, allow_none=True, required=False)
    valor_descricao = fields.String(allow_none=True, required=False)
    condicao = fields.String(allow_none=True, required=False)
    principal = fields.Boolean(required=False)
    moeda = fields.String(required=False)
    ativo = fields.Boolean(required=False)
    observacoes = fields.String(allow_none=True, required=False)


class PrepostoCredenciadoCreateSchema(Schema):
    nome = fields.String(required=True)
    cpf = fields.String(required=True)
    registro_rfb = fields.String(allow_none=True)
    categoria = fields.String(
        load_default="DESPACHANTE",
        validate=validate.OneOf(["DESPACHANTE", "AJUDANTE"]),
    )
    ativo = fields.Boolean(load_default=True)
    observacoes = fields.String(allow_none=True)

    @validates_schema
    def validate_cpf(self, data, **kwargs):
        cpf = "".join(character for character in data.get("cpf", "") if character.isdigit())
        if len(cpf) != 11:
            raise ValidationError("O CPF deve possuir 11 dígitos.", field_name="cpf")
        data["cpf"] = cpf


class PrepostoCredenciadoUpdateSchema(Schema):
    nome = fields.String(required=False)
    cpf = fields.String(required=False)
    registro_rfb = fields.String(allow_none=True, required=False)
    categoria = fields.String(
        required=False,
        validate=validate.OneOf(["DESPACHANTE", "AJUDANTE"]),
    )
    ativo = fields.Boolean(required=False)
    observacoes = fields.String(allow_none=True, required=False)

    @validates_schema
    def validate_cpf(self, data, **kwargs):
        if "cpf" not in data:
            return
        cpf = "".join(character for character in data["cpf"] if character.isdigit())
        if len(cpf) != 11:
            raise ValidationError("O CPF deve possuir 11 dígitos.", field_name="cpf")
        data["cpf"] = cpf


class PrepostoCredenciadoVinculoCreateSchema(Schema):
    credenciado_id = fields.UUID(required=True)
    observacoes = fields.String(allow_none=True)
