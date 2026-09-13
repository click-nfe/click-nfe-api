from flask import Flask, jsonify
from flask_cors import CORS
from marshmallow import ValidationError

from .config import Config
from .extensions import db


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    CORS(
        app,
        resources={r"/*": {"origins": "https://www.portalcl.online"}},
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    db.init_app(app)

    from .routes.auth_routes import auth_bp
    from .routes.dashboard_routes import dashboard_bp
    from .routes.scope_routes import scope_bp
    from .routes.user_routes import user_bp
    from .routes.admin_routes import admin_bp
    from .routes.prepostos import prepostos_bp
    from .routes.client_routes import client_bp
    from .routes.organization_routes import organization_bp
    from .routes.client_fiscal_profile_routes import client_fiscal_profile_bp
    from .routes.fiscal_certificate_routes import fiscal_certificate_bp
    from .routes.import_process_routes import import_process_bp
    from .routes.nfe_draft_routes import nfe_draft_bp
    from .routes.nfe_number_sequence_routes import nfe_number_sequence_bp
    from .routes.provider_connection_routes import provider_connection_bp
    from .routes.nfe_automation_routes import (
        client_import_tax_rule_bp,
        nfe_context_bp,
    )
    from .routes.nfe_carrier_routes import nfe_carrier_bp
    from .routes.fiscal_reference_routes import fiscal_reference_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(scope_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(prepostos_bp)
    app.register_blueprint(client_bp)
    app.register_blueprint(organization_bp)
    app.register_blueprint(import_process_bp)
    app.register_blueprint(provider_connection_bp)
    app.register_blueprint(nfe_draft_bp)
    app.register_blueprint(client_fiscal_profile_bp)
    app.register_blueprint(fiscal_certificate_bp)
    app.register_blueprint(nfe_number_sequence_bp)
    app.register_blueprint(client_import_tax_rule_bp)
    app.register_blueprint(nfe_context_bp)
    app.register_blueprint(nfe_carrier_bp)
    app.register_blueprint(fiscal_reference_bp)

    from .scope_cnae_cli import scope_cnae_cli
    from .preposto_catalog_cli import preposto_catalog_cli

    app.cli.add_command(scope_cnae_cli)
    app.cli.add_command(preposto_catalog_cli)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.errorhandler(ValidationError)
    def handle_validation_error(err):
        return jsonify({"error": "Validation error", "messages": err.messages}), 400

    return app
