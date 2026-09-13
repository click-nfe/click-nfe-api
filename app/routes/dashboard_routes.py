from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, jsonify, request

from ..auth import admin_required, roles_required
from ..services.dashboard_metrics_service import DashboardMetricsService

dashboard_bp = Blueprint("dashboards", __name__, url_prefix="/dashboards")


def _parse_bool(value, default=False) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    value = str(value).strip().lower()
    return value in ("1", "true", "sim", "s", "yes", "y")


def _parse_int(value, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    if min_value is not None and parsed < min_value:
        parsed = min_value

    if max_value is not None and parsed > max_value:
        parsed = max_value

    return parsed


def _parse_datetime(value):
    """
    Aceita:
    - YYYY-MM-DD
    - YYYY-MM-DDTHH:MM:SS
    - ISO com Z
    """
    if not value:
        return None

    raw = str(value).strip()

    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw)

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _common_filters() -> dict:
    return {
        "status": request.args.get("status") or None,
        "operation_type": request.args.get("operationType") or request.args.get("operation_type") or None,
        "service_code": request.args.get("serviceCode") or request.args.get("service_code") or None,
        "created_by_id": request.args.get("createdById") or request.args.get("created_by_id") or None,
        "responsible_user_id": request.args.get("responsibleUserId") or request.args.get("responsible_user_id") or None,
        "client_id": request.args.get("clientId") or request.args.get("client_id") or None,
        "q": request.args.get("q") or None,
        "date_from": _parse_datetime(request.args.get("dateFrom") or request.args.get("date_from")),
        "date_to": _parse_datetime(request.args.get("dateTo") or request.args.get("date_to")),
    }


@dashboard_bp.get("/admin")
@roles_required("comercial")
def admin_dashboard():
    """
    Endpoint legado mantido para compatibilidade com o front atual.
    """
    service = DashboardMetricsService(g.current_user)
    filters = _common_filters()

    overview = service.get_admin_metrics_overview(
        status=filters["status"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
    )

    scopes_by_user = service.get_scopes_by_user(
        status=filters["status"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        include_scopes=False,
    )

    return jsonify(
        {
            "createdLastMonth": 0,
            "outdatedScopes": 0,
            "scopesByPerson": [
                {
                    "group": item["userId"],
                    "name": item["userName"],
                    "total": item["totalScopes"],
                }
                for item in scopes_by_user["items"]
            ],
            "scopesBySector": [],
            "comercialAveragePrice": 0,
            "totalScopes": overview["totalScopes"],
            "totalEnabledServices": overview["totalEnabledServices"],
            "totalDistinctServices": overview["totalDistinctServices"],
            "totalServicesAmount": overview["totalServicesAmount"],
        }
    )


@dashboard_bp.get("/admin/metrics")
@roles_required("comercial")
def admin_metrics_overview():
    service = DashboardMetricsService(g.current_user)
    filters = _common_filters()

    data = service.get_admin_metrics_overview(
        status=filters["status"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
    )

    return jsonify(data)


@dashboard_bp.get("/admin/scopes-by-user")
@roles_required("comercial")
def admin_scopes_by_user():
    service = DashboardMetricsService(g.current_user)
    filters = _common_filters()

    group_by = (
        request.args.get("groupBy")
        or request.args.get("group_by")
        or "created_by"
    )

    data = service.get_scopes_by_user(
        group_by=group_by,
        status=filters["status"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
    )

    return jsonify(data)

@dashboard_bp.get("/admin/users/<user_id>/scopes")
@roles_required("comercial")
def admin_scopes_for_user(user_id):
    service = DashboardMetricsService(g.current_user)
    filters = _common_filters()

    group_by = (
        request.args.get("groupBy")
        or request.args.get("group_by")
        or "created_by"
    )

    limit = _parse_int(
        request.args.get("limit"),
        default=500,
        min_value=1,
        max_value=500,
    )

    offset = _parse_int(
        request.args.get("offset"),
        default=0,
        min_value=0,
    )

    data = service.get_scopes_for_user(
        user_id=user_id,
        group_by=group_by,
        status=filters["status"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        limit=limit,
        offset=offset,
    )

    return jsonify(data)


@dashboard_bp.get("/admin/clients-by-user")
@roles_required("comercial")
def admin_clients_by_user():
    service = DashboardMetricsService(g.current_user)
    filters = _common_filters()

    group_by = (
        request.args.get("groupBy")
        or request.args.get("group_by")
        or "analista_da"
    )

    include_clients = _parse_bool(
        request.args.get("includeClients") or request.args.get("include_clients"),
        default=False,
    )

    clients_limit_per_user = _parse_int(
        request.args.get("clientsLimitPerUser") or request.args.get("clients_limit_per_user"),
        default=10,
        min_value=1,
        max_value=100,
    )

    data = service.get_clients_by_user(
        group_by=group_by,
        status=filters["status"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        include_clients=include_clients,
        clients_limit_per_user=clients_limit_per_user,
    )

    return jsonify(data)


@dashboard_bp.get("/admin/users/<user_id>/clients")
@roles_required("comercial")
def admin_clients_for_user(user_id):
    service = DashboardMetricsService(g.current_user)
    filters = _common_filters()

    group_by = (
        request.args.get("groupBy")
        or request.args.get("group_by")
        or "analista_da"
    )

    limit = _parse_int(
        request.args.get("limit"),
        default=500,
        min_value=1,
        max_value=500,
    )

    offset = _parse_int(
        request.args.get("offset"),
        default=0,
        min_value=0,
    )

    data = service.get_clients_for_user(
        user_id=user_id,
        group_by=group_by,
        status=filters["status"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        limit=limit,
        offset=offset,
    )

    return jsonify(data)



@dashboard_bp.get("/admin/services")
@roles_required("comercial")
def admin_services_summary():
    service = DashboardMetricsService(g.current_user)
    filters = _common_filters()

    data = service.get_services_summary(
        status=filters["status"],
        operation_type=filters["operation_type"],
        service_code=filters["service_code"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
    )

    return jsonify(data)


@dashboard_bp.get("/admin/services/by-scope")
@roles_required("comercial")
def admin_services_by_scope():
    service = DashboardMetricsService(g.current_user)
    filters = _common_filters()

    limit = _parse_int(request.args.get("limit"), default=50, min_value=1, max_value=500)
    offset = _parse_int(request.args.get("offset"), default=0, min_value=0)

    data = service.get_services_by_scope(
        status=filters["status"],
        operation_type=filters["operation_type"],
        service_code=filters["service_code"],
        created_by_id=filters["created_by_id"],
        responsible_user_id=filters["responsible_user_id"],
        client_id=filters["client_id"],
        q=filters["q"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        limit=limit,
        offset=offset,
    )

    return jsonify(data)


@dashboard_bp.get("/credenciamento")
@roles_required("credenciamento")
def credenciamento_dashboard():
    return jsonify(
        {
            "createdLastMonth": 0,
            "expiredScopes": 0,
            "createdByUser": 0,
            "waitingAdjustment": 0,
        }
    )


@dashboard_bp.get("/operacao")
@roles_required("operacao")
def operacao_dashboard():
    return jsonify(
        {
            "responsibleScopes": 0,
            "createdLastMonth": 0,
            "waitingAdjustment": 0,
        }
    )
