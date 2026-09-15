from marshmallow import Schema, fields, validate


class LoginSchema(Schema):
    email = fields.Email(required=True)
    password = fields.String(required=True)


class RegisterSchema(Schema):
    nome = fields.String(required=True)
    email = fields.Email(required=True)
    password = fields.String(required=True, load_only=True, validate=validate.Length(min=8))
    setor = fields.String(allow_none=True)

    organization_nome = fields.String(required=True)
    organization_slug = fields.String(load_default=None, allow_none=True)
    organization_cnpj = fields.String(load_default=None, allow_none=True)


class RefreshSchema(Schema):
    refreshToken = fields.String(required=True)
