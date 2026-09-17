from marshmallow import Schema, fields


class LoginSchema(Schema):
    email = fields.Email(required=True)
    password = fields.String(required=True)


class RefreshSchema(Schema):
    refreshToken = fields.String(required=True)
