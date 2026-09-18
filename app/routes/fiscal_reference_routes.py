from flask import Blueprint, current_app, jsonify, request
from marshmallow import ValidationError

from ..auth import auth_required
from ..schemas.fiscal_reference import (
    CountryReferenceQuerySchema,
    FiscalCountrySchema,
    FiscalMunicipalitySchema,
    MunicipalityReferenceQuerySchema,
)
from ..services.fiscal_reference import FiscalReferenceService
from ..integrations.postal_code import (
    BrasilApiCepProvider,
    PostalCodeNotFoundError,
    ViaCepProvider,
)
from ..services.postal_code import (
    PostalCodeLookupService,
    PostalCodeUnavailableError,
)
from ..extensions import db
from .route_helpers import validation_error_response


fiscal_reference_bp = Blueprint(
    "fiscal_reference",
    __name__,
    url_prefix="/fiscal-reference",
)

municipality_query_schema = MunicipalityReferenceQuerySchema()
country_query_schema = CountryReferenceQuerySchema()
municipality_list_schema = FiscalMunicipalitySchema(many=True)
country_list_schema = FiscalCountrySchema(many=True)


def _postal_code_service() -> PostalCodeLookupService:
    timeout = current_app.config.get("CEP_LOOKUP_TIMEOUT_SECONDS", 4)
    return PostalCodeLookupService(
        providers=[
            ViaCepProvider(
                base_url=current_app.config.get(
                    "VIA_CEP_BASE_URL",
                    "https://viacep.com.br",
                ),
                timeout_seconds=timeout,
            ),
            BrasilApiCepProvider(
                base_url=current_app.config.get(
                    "BRASIL_API_BASE_URL",
                    "https://brasilapi.com.br/api",
                ),
                timeout_seconds=timeout,
            ),
        ],
        cache_ttl_seconds=current_app.config.get(
            "CEP_CACHE_TTL_SECONDS",
            2_592_000,
        ),
    )


@fiscal_reference_bp.get("/postal-codes/<zip_code>")
@auth_required
def get_postal_code(zip_code: str):
    try:
        result = _postal_code_service().lookup(zip_code)
        db.session.commit()
        return jsonify(result)
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": "invalid_zip_code", "message": str(exc)}), 400
    except PostalCodeNotFoundError as exc:
        db.session.rollback()
        return jsonify({"error": "postal_code_not_found", "message": str(exc)}), 404
    except PostalCodeUnavailableError as exc:
        db.session.rollback()
        return jsonify(
            {"error": "postal_code_lookup_unavailable", "message": str(exc)}
        ), 503


@fiscal_reference_bp.get("/municipalities")
@auth_required
def list_fiscal_municipalities():
    try:
        params = municipality_query_schema.load(request.args)
    except ValidationError as exc:
        return validation_error_response(exc)

    rows = FiscalReferenceService.search_municipalities(
        query=params["q"],
        state=params["state"],
        limit=params["limit"],
    )
    return jsonify(
        {
            "items": municipality_list_schema.dump(rows),
            "total": len(rows),
            "limit": params["limit"],
            "q": params["q"],
            "state": (params["state"] or "").upper() or None,
        }
    )


@fiscal_reference_bp.get("/countries")
@auth_required
def list_fiscal_countries():
    try:
        params = country_query_schema.load(request.args)
    except ValidationError as exc:
        return validation_error_response(exc)

    rows = FiscalReferenceService.search_countries(
        query=params["q"],
        active_on=params["active_on"],
        limit=params["limit"],
    )
    return jsonify(
        {
            "items": country_list_schema.dump(rows),
            "total": len(rows),
            "limit": params["limit"],
            "q": params["q"],
            "active_on": params["active_on"].isoformat(),
        }
    )
