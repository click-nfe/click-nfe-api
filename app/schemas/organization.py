from marshmallow_sqlalchemy import SQLAlchemyAutoSchema

from app.models import (
    Organization,
)


class OrganizationSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = Organization
        load_instance = True
        exclude = ("created_at", "updated_at")
