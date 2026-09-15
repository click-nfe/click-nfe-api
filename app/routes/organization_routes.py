from flask import Blueprint, g, jsonify

from ..auth import auth_required
from ..models import Organization
from ..schemas import OrganizationSchema

organization_bp = Blueprint("organizations", __name__, url_prefix="/organizations")
organization_schema = OrganizationSchema()


@organization_bp.get("/me")
@auth_required
def get_my_organization():
    if not g.current_user.organization_id:
        return jsonify({"error": "Usuário sem organização"}), 400

    org = Organization.query.get_or_404(g.current_user.organization_id)
    return jsonify({"organization": organization_schema.dump(org)})
