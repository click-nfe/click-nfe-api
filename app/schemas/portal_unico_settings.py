from marshmallow import Schema, fields, validate


class ConfigurePortalUnicoSchema(Schema):
    client_id = fields.String(
        required=True,
        validate=validate.Length(min=1, max=512),
    )
    client_secret = fields.String(
        required=True,
        load_only=True,
        validate=validate.Length(min=1, max=2048),
    )
    role_type = fields.String(
        load_default="IMPEXP",
        validate=validate.Equal(
            "IMPEXP",
            error="A consulta de DUIMP requer Role-Type IMPEXP.",
        ),
    )
