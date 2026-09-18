from marshmallow import Schema, fields, validate

from app.models.import_process import FiscalEnvironment
from app.models.nfe_issuance import FiscalCredentialProvider


class RegisterFiscalCertificateSchema(Schema):
    environment = fields.String(
        required=True,
        validate=validate.OneOf(
            [item.value for item in FiscalEnvironment]
        ),
    )
    provider = fields.String(
        load_default=FiscalCredentialProvider.GCP_SECRET_MANAGER.value,
        validate=validate.OneOf(
            [FiscalCredentialProvider.GCP_SECRET_MANAGER.value]
        ),
    )
    certificate_ref = fields.String(
        required=True,
        validate=validate.Length(min=1, max=500),
    )
    password_ref = fields.String(
        required=True,
        validate=validate.Length(min=1, max=500),
    )


class UploadFiscalCertificateSchema(Schema):
    environment = fields.String(
        required=True,
        validate=validate.OneOf(
            [item.value for item in FiscalEnvironment]
        ),
    )
    password = fields.String(
        required=True,
        load_only=True,
        validate=validate.Length(min=1, max=1024),
    )


class SignNfeXmlSchema(Schema):
    certificate_id = fields.UUID(load_default=None, allow_none=True)
