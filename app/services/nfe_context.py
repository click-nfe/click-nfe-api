from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.nfe_reference import NFE_TRANSPORT_MODE_CODES


class NfeContextResolver:
    """Consolida fontes da NF-e sem transformar inferências em fatos fiscais."""

    REQUIRED_DUIMP_FIELDS = (
        "registration_date",
        "clearance_location",
        "clearance_state",
        "clearance_date",
        "transport_mode_code",
    )
    ALLOWED_OVERRIDES = {
        "clearance_location",
        "clearance_state",
        "clearance_date",
        "transport_mode_code",
        "intermediation_type",
        "third_party_tax_id",
        "third_party_state",
    }

    def resolve(
        self,
        *,
        normalized: Mapping[str, Any],
        external: Mapping[str, Any] | None = None,
        connection_config: Mapping[str, Any] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = deepcopy(dict(normalized))
        external = dict(external or {})
        config = dict(connection_config or {})
        overrides = dict(overrides or {})
        previous_sources = dict(resolved.get("automation_field_sources") or {})
        sources: dict[str, str] = {}

        for field in self.REQUIRED_DUIMP_FIELDS:
            if resolved.get(field) not in (None, ""):
                sources[field] = previous_sources.get(field) or "duimp"

        tabx_unit = self._first_tabx_row(external.get("customs_unit"))
        if tabx_unit:
            self._set_if_missing(
                resolved,
                sources,
                "clearance_location",
                self._tabx_value(
                    tabx_unit,
                    config.get("tabx_customs_unit_description_field", "NOME"),
                ),
                "portal_unico_tabx",
            )
            self._set_if_missing(
                resolved,
                sources,
                "clearance_state",
                self._tabx_value(
                    tabx_unit,
                    config.get("tabx_customs_unit_state_field", "UF"),
                ),
                "portal_unico_tabx",
            )
        else:
            reference_unit = external.get("cached_customs_unit")
            if isinstance(reference_unit, Mapping):
                self._set_if_missing(
                    resolved,
                    sources,
                    "clearance_location",
                    reference_unit.get("description"),
                    "local_fiscal_reference",
                )
                self._set_if_missing(
                    resolved,
                    sources,
                    "clearance_state",
                    reference_unit.get("state"),
                    "local_fiscal_reference",
                )

        knowledge = self._first_active_knowledge(external.get("cargo_knowledge"))
        if knowledge:
            knowledge_type = str(knowledge.get("tipo") or "").upper()
            airport = knowledge.get("codigoAeroportoDestinoConhecimento")
            if knowledge_type in {"AWB", "HAWB", "MAWB", "DSIC"} or airport:
                self._set_if_missing(
                    resolved,
                    sources,
                    "transport_mode_code",
                    "4",
                    "portal_unico_cct",
                )
            # O IATA é uma referência oficial, mas não substitui a descrição da
            # unidade aduaneira quando ela estiver disponível no TABX.
            self._set_if_missing(
                resolved,
                sources,
                "clearance_location",
                airport,
                "portal_unico_cct",
            )

        country_iso = (
            (resolved.get("foreign_supplier") or {}).get("country_iso_alpha_2")
            or (resolved.get("country_of_origin") or {}).get("iso_alpha_2")
        )
        country_map = config.get("country_code_map") or {}
        country_code = country_map.get(str(country_iso or "").upper())
        supplier = deepcopy(resolved.get("foreign_supplier") or {})
        tabx_country = self._first_tabx_row(external.get("country"))
        if tabx_country:
            country_code = country_code or self._tabx_value(
                tabx_country,
                config.get("tabx_country_code_field", "CODIGO"),
            )
            country_name = self._tabx_value(
                tabx_country,
                config.get("tabx_country_name_field", "NOME"),
            )
            if country_name and not supplier.get("country_name"):
                supplier["country_name"] = str(country_name)
                sources["foreign_supplier.country_name"] = "portal_unico_tabx"
        if country_code and not supplier.get("country_code"):
            supplier["country_code"] = str(country_code)
            supplier.setdefault("country_iso_alpha_2", country_iso)
            resolved["foreign_supplier"] = supplier
            sources["foreign_supplier.country_code"] = (
                "portal_unico_tabx" if tabx_country else "provider_configuration"
            )
        elif not supplier.get("country_code"):
            reference_country = external.get("cached_country")
            if isinstance(reference_country, Mapping):
                supplier["country_code"] = reference_country.get("code")
                supplier.setdefault("country_iso_alpha_2", country_iso)
                if not supplier.get("country_name"):
                    supplier["country_name"] = reference_country.get("name")
                    sources["foreign_supplier.country_name"] = (
                        "local_fiscal_reference"
                    )
                resolved["foreign_supplier"] = supplier
                sources["foreign_supplier.country_code"] = (
                    "local_fiscal_reference"
                )
        elif supplier != (resolved.get("foreign_supplier") or {}):
            resolved["foreign_supplier"] = supplier

        for field, value in overrides.items():
            if field == "foreign_supplier" and isinstance(value, Mapping):
                supplier = deepcopy(resolved.get("foreign_supplier") or {})
                supplier.update(dict(value))
                resolved["foreign_supplier"] = supplier
                for key in value:
                    sources[f"foreign_supplier.{key}"] = "operator_override"
                continue
            if field not in self.ALLOWED_OVERRIDES:
                continue
            if value not in (None, ""):
                resolved[field] = value
                sources[field] = "operator_override"

        transport_mode_code = resolved.get("transport_mode_code")
        if transport_mode_code not in (None, ""):
            transport_mode_code = str(transport_mode_code).strip()
            if transport_mode_code in NFE_TRANSPORT_MODE_CODES:
                resolved["transport_mode_code"] = transport_mode_code
            else:
                resolved["transport_mode_code"] = None
                sources.pop("transport_mode_code", None)

        resolved["automation_field_sources"] = {
            **previous_sources,
            **sources,
        }

        fields = {
            field: self._field(
                resolved.get(field),
                sources.get(field) or previous_sources.get(field),
            )
            for field in self.REQUIRED_DUIMP_FIELDS
        }
        supplier = resolved.get("foreign_supplier") or {}
        supplier_country_code = supplier.get("country_code")
        supplier_country_name = supplier.get("country_name")
        fields["foreign_supplier.country_code"] = self._field(
            supplier_country_code,
            sources.get("foreign_supplier.country_code")
            or previous_sources.get("foreign_supplier.country_code"),
        )
        fields["foreign_supplier.country_name"] = self._field(
            supplier_country_name,
            sources.get("foreign_supplier.country_name")
            or previous_sources.get("foreign_supplier.country_name"),
        )

        missing = [
            field
            for field in self.REQUIRED_DUIMP_FIELDS
            if resolved.get(field) in (None, "")
        ]
        if not supplier_country_code:
            missing.append("foreign_supplier.country_code")
        if not supplier_country_name:
            missing.append("foreign_supplier.country_name")

        pcce = external.get("icms_declaration")
        suggested_costs: dict[str, str] = {}
        if resolved.get("afrmm_value") not in (None, ""):
            suggested_costs["afrmm"] = str(resolved["afrmm_value"])
        tax_totals = resolved.get("tax_totals") or {}
        for tax_name in (
            "taxa_utilizacao",
            "taxa_utilizacao_siscomex",
            "taxa_siscomex",
        ):
            tax = tax_totals.get(tax_name)
            value = tax.get("value") if isinstance(tax, Mapping) else tax
            if value not in (None, ""):
                suggested_costs["siscomex_fee"] = str(value)
                break
        if isinstance(pcce, Mapping):
            if pcce.get("valorAfrmm") is not None:
                suggested_costs["afrmm"] = str(pcce["valorAfrmm"])
            if pcce.get("valorDespesasAduaneiras") is not None:
                suggested_costs["other"] = str(pcce["valorDespesasAduaneiras"])
        if suggested_costs:
            resolved["automation_additional_costs"] = suggested_costs

        fiscal_icms_reference = {
            "declared_value": (
                str(pcce.get("valorIcms"))
                if isinstance(pcce, Mapping)
                and pcce.get("valorIcms") is not None
                else None
            ),
            "paid_value": (
                str(pcce.get("valorPagoIcms"))
                if isinstance(pcce, Mapping)
                and pcce.get("valorPagoIcms") is not None
                else None
            ),
            "favored_state": (
                pcce.get("ufFavorecida") if isinstance(pcce, Mapping) else None
            ),
            "source": "portal_unico_pcce" if isinstance(pcce, Mapping) else None,
        }
        if isinstance(pcce, Mapping):
            resolved["automation_fiscal_references"] = {
                "icms": fiscal_icms_reference
            }

        return {
            "normalized": resolved,
            "fields": fields,
            "missing_fields": missing,
            "ready_for_draft": not missing,
            "external": external,
            "suggested": {
                "duimp_overrides": {
                    field: resolved.get(field)
                    for field in self.ALLOWED_OVERRIDES
                    if resolved.get(field) not in (None, "")
                },
                "foreign_supplier": resolved.get("foreign_supplier"),
                "additional_costs": suggested_costs,
            },
            "fiscal_references": {
                "icms": fiscal_icms_reference
            },
        }

    @staticmethod
    def _set_if_missing(
        resolved: dict[str, Any],
        sources: dict[str, str],
        field: str,
        value: Any,
        source: str,
    ) -> None:
        if resolved.get(field) in (None, "") and value not in (None, ""):
            resolved[field] = str(value)
            sources[field] = source

    @staticmethod
    def _field(value: Any, source: str | None) -> dict[str, Any]:
        if value in (None, ""):
            return {"value": None, "source": None, "status": "missing"}
        return {
            "value": value,
            "source": source or "duimp",
            "status": "resolved",
        }

    @staticmethod
    def _first_active_knowledge(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, list):
            return None
        for item in payload:
            if isinstance(item, dict) and item.get("situacao") != "E":
                return item
        return None

    @classmethod
    def _first_tabx_row(cls, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, Mapping):
            return None
        rows = payload.get("dados")
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        if not isinstance(row, Mapping):
            return None
        fields = row.get("campos")
        if not isinstance(fields, list):
            return dict(row)
        result: dict[str, Any] = {}
        for field in fields:
            if isinstance(field, Mapping) and field.get("nome"):
                result[str(field["nome"]).upper()] = field.get("valor")
        return result

    @staticmethod
    def _tabx_value(row: Mapping[str, Any], field_name: Any) -> Any:
        wanted = str(field_name or "").upper()
        for key, value in row.items():
            if str(key).upper() == wanted:
                return value
        return None
