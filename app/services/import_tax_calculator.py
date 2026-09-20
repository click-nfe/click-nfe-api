from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any


class ImportTaxCalculationError(ValueError):
    pass


class ImportTaxCalculator:
    MONEY = Decimal("0.01")
    RATE = Decimal("0.0001")
    NON_TAXED_ICMS_CSTS = {"40", "41", "50"}
    REDUCED_BASE_ICMS_CSTS = {"20", "70"}
    ST_ICMS_CSTS = {"10", "30", "70"}
    RETAINED_ST_ICMS_CSTS = {"60"}
    REVIEWABLE_ICMS_CSTS = REDUCED_BASE_ICMS_CSTS | ST_ICMS_CSTS | RETAINED_ST_ICMS_CSTS
    SUPPORTED_ICMS_CSTS = NON_TAXED_ICMS_CSTS | {
        "00",
        "10",
        "20",
        "30",
        "51",
        "60",
        "70",
        "90",
    }

    def calculate(
        self,
        items: list[dict[str, Any]],
        *,
        configuration: dict[str, Any],
        additional_costs: dict[str, Any] | None = None,
        preallocated_costs: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        if not items:
            raise ImportTaxCalculationError("Não existem itens para calcular.")

        icms_cst = str(configuration.get("icms_cst") or "90").zfill(2)
        if icms_cst not in self.SUPPORTED_ICMS_CSTS:
            raise ImportTaxCalculationError(
                "ICMS CST não suportado. Informe 00, 10, 20, 30, 40, 41, 50, "
                "51, 60, 70 ou 90."
            )
        raw_icms_rate = configuration.get("icms_rate")
        has_icms_rate = raw_icms_rate not in (None, "")
        icms_rate = self._decimal(raw_icms_rate)
        raw_tax_treatment_confirmed = configuration.get(
            "icms_tax_treatment_confirmed"
        )
        if raw_tax_treatment_confirmed not in (None, True, False):
            raise ImportTaxCalculationError(
                "icms_tax_treatment_confirmed deve ser booleano."
            )
        tax_treatment_confirmed = raw_tax_treatment_confirmed is True
        deferment_rate = self._decimal(
            configuration.get("icms_deferment_rate")
        )
        raw_base_reduction_rate = configuration.get(
            "icms_base_reduction_rate"
        )
        has_base_reduction = raw_base_reduction_rate not in (None, "")
        base_reduction_rate = self._decimal(raw_base_reduction_rate)
        st_base_method = str(configuration.get("icms_st_base_method") or "6")
        raw_st_mva_rate = configuration.get("icms_st_mva_rate")
        has_st_mva_rate = raw_st_mva_rate not in (None, "")
        st_mva_rate = self._decimal(raw_st_mva_rate)
        raw_st_base_reduction_rate = configuration.get(
            "icms_st_base_reduction_rate"
        )
        has_st_base_reduction = raw_st_base_reduction_rate not in (None, "")
        st_base_reduction_rate = self._decimal(raw_st_base_reduction_rate)
        raw_st_rate = configuration.get("icms_st_rate")
        has_st_rate = raw_st_rate not in (None, "")
        st_rate = self._decimal(raw_st_rate)

        if icms_cst in self.NON_TAXED_ICMS_CSTS | {"30", "60"}:
            if has_icms_rate:
                raise ImportTaxCalculationError(
                    f"ICMS CST {icms_cst} não aceita alíquota nominal."
                )
        elif icms_cst == "51":
            if deferment_rate and not (
                Decimal("0") < deferment_rate <= Decimal("100")
            ):
                raise ImportTaxCalculationError(
                    "O percentual de diferimento do ICMS CST 51 deve ser maior "
                    "que zero e menor ou igual a 100."
                )
            if has_base_reduction and not (
                Decimal("0") < base_reduction_rate <= Decimal("100")
            ):
                raise ImportTaxCalculationError(
                    "O percentual de redução da base do ICMS CST 51 deve ser "
                    "maior que zero e menor ou igual a 100."
                )
            if not deferment_rate and not has_base_reduction:
                raise ImportTaxCalculationError(
                    "ICMS CST 51 exige percentual de diferimento ou de redução "
                    "da base de cálculo."
                )
            if has_icms_rate and not Decimal("0") < icms_rate < Decimal("100"):
                raise ImportTaxCalculationError(
                    "A alíquota nominal do ICMS, quando informada, deve ser "
                    "maior que zero e menor que 100."
                )
        elif not Decimal("0") < icms_rate < Decimal("100"):
            raise ImportTaxCalculationError(
                "A alíquota de ICMS deve ser maior que zero e menor que 100."
            )

        if icms_cst in self.REDUCED_BASE_ICMS_CSTS and not (
            has_base_reduction
            and Decimal("0") < base_reduction_rate <= Decimal("100")
        ):
            raise ImportTaxCalculationError(
                f"ICMS CST {icms_cst} exige percentual de redução da base maior "
                "que zero e menor ou igual a 100."
            )

        if icms_cst in self.ST_ICMS_CSTS:
            if st_base_method not in {"4", "6"}:
                raise ImportTaxCalculationError(
                    "Nesta etapa, a modalidade da base do ICMS ST deve ser 4 "
                    "(MVA) ou 6 (valor da operação)."
                )
            if st_base_method == "4" and not has_st_mva_rate:
                raise ImportTaxCalculationError(
                    "ICMS ST com modalidade 4 exige o percentual de MVA."
                )
            if not Decimal("0") <= st_mva_rate <= Decimal("999.9999"):
                raise ImportTaxCalculationError(
                    "O percentual de MVA do ICMS ST deve estar entre zero e "
                    "999.9999."
                )
            if has_st_base_reduction and not (
                Decimal("0") <= st_base_reduction_rate <= Decimal("100")
            ):
                raise ImportTaxCalculationError(
                    "O percentual de redução da base do ICMS ST deve estar entre "
                    "zero e 100."
                )
            if not has_st_rate or not Decimal("0") < st_rate < Decimal("100"):
                raise ImportTaxCalculationError(
                    "ICMS CST com substituição tributária exige alíquota ST maior "
                    "que zero e menor que 100."
                )

        retained_st_base = self._money(
            configuration.get("icms_st_retained_base")
        )
        retained_st_rate = self._decimal(
            configuration.get("icms_st_retained_rate")
        )
        retained_st_value = self._money(
            configuration.get("icms_st_retained_value")
        )
        if icms_cst in self.RETAINED_ST_ICMS_CSTS:
            required_retained_fields = {
                "icms_st_retained_base": configuration.get(
                    "icms_st_retained_base"
                ),
                "icms_st_retained_rate": configuration.get(
                    "icms_st_retained_rate"
                ),
                "icms_st_retained_value": configuration.get(
                    "icms_st_retained_value"
                ),
            }
            missing = [
                name
                for name, value in required_retained_fields.items()
                if value in (None, "")
            ]
            if missing:
                raise ImportTaxCalculationError(
                    "ICMS CST 60 exige base, alíquota e valor do ICMS ST retido "
                    "anteriormente."
                )
            if retained_st_base < 0 or retained_st_value < 0:
                raise ImportTaxCalculationError(
                    "Base e valor do ICMS ST retido não podem ser negativos."
                )
            if not Decimal("0") <= retained_st_rate <= Decimal("100"):
                raise ImportTaxCalculationError(
                    "A alíquota do ICMS ST retido deve estar entre zero e 100."
                )

        costs = additional_costs or {}
        cost_totals = {
            "afrmm": self._money(costs.get("afrmm")),
            "siscomex_fee": self._money(costs.get("siscomex_fee")),
            "thc": self._money(costs.get("thc")),
            "other": self._money(costs.get("other")),
        }
        negative_costs = [
            name for name, value in cost_totals.items() if value < 0
        ]
        if negative_costs:
            raise ImportTaxCalculationError(
                "Despesas adicionais não podem ser negativas: "
                + ", ".join(negative_costs)
                + "."
            )
        customs_value_weights = [
            self._decimal(item.get("customs_value") or item.get("product_value"))
            for item in items
        ]
        net_weight_weights = [
            self._decimal(item.get("net_weight") or item.get("allocation_weight"))
            for item in items
        ]

        customs_cost_total = sum(
            (
                cost_totals["siscomex_fee"],
                cost_totals["thc"],
                cost_totals["other"],
            ),
            Decimal("0"),
        )
        if customs_cost_total and any(
            weight <= 0 for weight in customs_value_weights
        ):
            raise ImportTaxCalculationError(
                "O valor aduaneiro positivo de todos os itens é obrigatório "
                "para ratear as despesas adicionais."
            )

        if preallocated_costs is None and cost_totals["afrmm"] and len(items) > 1 and any(
            weight <= 0 for weight in net_weight_weights
        ):
            raise ImportTaxCalculationError(
                "O peso líquido positivo de todos os itens é obrigatório para "
                "ratear o AFRMM."
            )

        if preallocated_costs is not None:
            if len(preallocated_costs) != len(items):
                raise ImportTaxCalculationError(
                    "O rateio persistido precisa corresponder aos itens da NF-e."
                )
            allocations = {
                name: [
                    self._money(row.get(name))
                    for row in preallocated_costs
                ]
                for name in ("afrmm", "siscomex_fee", "thc", "other")
            }
            for name, values in allocations.items():
                if sum(values, Decimal("0")) != cost_totals[name]:
                    raise ImportTaxCalculationError(
                        f"O rateio persistido de {name} não confere com o total da NF-e."
                    )
        else:
            allocations = {
                "afrmm": self.allocate(
                    cost_totals["afrmm"],
                    net_weight_weights if len(items) > 1 else [Decimal("1")],
                ),
                "siscomex_fee": self.allocate(
                    cost_totals["siscomex_fee"], customs_value_weights
                ),
                "thc": self.allocate(cost_totals["thc"], customs_value_weights),
                "other": self.allocate(cost_totals["other"], customs_value_weights),
            }

        icms_base_allocations: list[Decimal] | None = None
        if (
            icms_cst == "51"
            and configuration.get(
                "icms_base_allocation", "proportional_customs_value"
            )
            == "proportional_customs_value"
        ):
            total_numerator = Decimal("0")
            for index, source in enumerate(items):
                source_taxes = deepcopy(
                    source.get("tax_payload") or source.get("taxes") or {}
                )
                total_numerator += (
                    self._money(source.get("product_value"))
                    + self._money(source.get("freight_value"))
                    + self._money(source.get("insurance_value"))
                    + self._tax(source_taxes, "ii")["value"]
                    + self._tax(source_taxes, "ipi")["value"]
                    + self._tax(source_taxes, "pis")["value"]
                    + self._tax(source_taxes, "cofins")["value"]
                    + self._money(source.get("other_value"))
                    + sum(
                        (values[index] for values in allocations.values()),
                        Decimal("0"),
                    )
                    - self._money(source.get("discount_value"))
                )
            effective_rate = (
                icms_rate
                * (Decimal("1") - base_reduction_rate / Decimal("100"))
                * (Decimal("1") - deferment_rate / Decimal("100"))
                if has_icms_rate
                else Decimal("0")
            )
            total_icms_base = self._money(
                total_numerator
                / (Decimal("1") - effective_rate / Decimal("100"))
            )
            icms_base_allocations = self.allocate(
                total_icms_base,
                customs_value_weights,
            )

        calculated_items: list[dict[str, Any]] = []
        for index, source in enumerate(items):
            item = deepcopy(source)
            existing_other = self._money(item.get("other_value"))
            item_costs = {
                name: values[index] for name, values in allocations.items()
            }
            item_afrmm = item_costs["afrmm"]
            item_other = existing_other + sum(item_costs.values(), Decimal("0"))
            item["other_value"] = self._format_money(item_other)
            item["cost_allocation"] = {
                name: self._format_money(value)
                for name, value in item_costs.items()
            }

            taxes = deepcopy(item.get("tax_payload") or item.get("taxes") or {})
            source_icms = deepcopy(taxes.get("icms") or {})
            ii = self._tax(taxes, "ii")
            ipi = self._tax(taxes, "ipi")
            pis = self._tax(taxes, "pis")
            cofins = self._tax(taxes, "cofins")

            product_value = self._money(item.get("product_value"))
            freight_value = self._money(item.get("freight_value"))
            insurance_value = self._money(item.get("insurance_value"))
            discount = self._money(item.get("discount_value"))
            icms_base_numerator = (
                product_value
                + freight_value
                + insurance_value
                + ii["value"]
                + ipi["value"]
                + pis["value"]
                + cofins["value"]
                + item_other
                - discount
            )
            icms_full_base = None
            if icms_cst in self.NON_TAXED_ICMS_CSTS | {"30", "60"}:
                icms_base = Decimal("0.00")
                icms_operation_value = None
                icms_deferred_value = None
                icms_value = Decimal("0.00")
            elif icms_cst == "51":
                effective_rate = (
                    icms_rate
                    * (
                        Decimal("1")
                        - base_reduction_rate / Decimal("100")
                    )
                    * (Decimal("1") - deferment_rate / Decimal("100"))
                    if has_icms_rate
                    else Decimal("0")
                )
                icms_base = (
                    icms_base_allocations[index]
                    if icms_base_allocations is not None
                    else self._money(
                        icms_base_numerator
                        / (Decimal("1") - effective_rate / Decimal("100"))
                    )
                )
                icms_operation_value = (
                    self._money(icms_base * icms_rate / Decimal("100"))
                    if has_icms_rate
                    else None
                )
                icms_deferred_value = (
                    self._money(
                        icms_operation_value
                        * deferment_rate
                        / Decimal("100")
                    )
                    if icms_operation_value is not None
                    else None
                )
                calculated_icms_value = (
                    self._money(icms_operation_value - icms_deferred_value)
                    if icms_operation_value is not None
                    else Decimal("0.00")
                )
                icms_value = (
                    None
                    if not has_icms_rate and has_base_reduction
                    else calculated_icms_value
                )
            elif icms_cst in self.REDUCED_BASE_ICMS_CSTS:
                effective_rate = icms_rate * (
                    Decimal("1") - base_reduction_rate / Decimal("100")
                )
                icms_full_base = self._money(
                    icms_base_numerator
                    / (Decimal("1") - effective_rate / Decimal("100"))
                )
                icms_base = self._money(
                    icms_full_base
                    * (Decimal("1") - base_reduction_rate / Decimal("100"))
                )
                icms_operation_value = None
                icms_deferred_value = None
                icms_value = self._money(
                    icms_base * icms_rate / Decimal("100")
                )
            else:
                icms_full_base = self._money(
                    icms_base_numerator
                    / (Decimal("1") - icms_rate / Decimal("100"))
                )
                icms_base = icms_full_base
                icms_operation_value = None
                icms_deferred_value = None
                icms_value = self._money(
                    icms_base * icms_rate / Decimal("100")
                )

            st_base = Decimal("0.00")
            st_value = Decimal("0.00")
            if icms_cst in self.ST_ICMS_CSTS:
                st_source_base = icms_full_base or icms_base_numerator
                if st_base_method == "4":
                    st_source_base *= Decimal("1") + st_mva_rate / Decimal(
                        "100"
                    )
                st_base = self._money(
                    st_source_base
                    * (
                        Decimal("1")
                        - st_base_reduction_rate / Decimal("100")
                    )
                )
                st_total = self._money(st_base * st_rate / Decimal("100"))
                st_value = max(
                    st_total - (icms_value or Decimal("0.00")),
                    Decimal("0.00"),
                )

            taxes["icms"] = {
                "origin": str(configuration.get("icms_origin") or "1"),
                "cst": icms_cst,
                "base_method": str(configuration.get("icms_base_method") or "3"),
                "base": self._format_money(icms_base),
                "base_reduction_rate": (
                    self._format_rate(base_reduction_rate)
                    if icms_cst in self.REDUCED_BASE_ICMS_CSTS | {"51"}
                    and has_base_reduction
                    else None
                ),
                "base_benefit_code": (
                    configuration.get("icms_base_benefit_code")
                    if icms_cst == "51"
                    else None
                ),
                "reference_base": (
                    self._format_money(icms_base_numerator)
                    if icms_cst in self.NON_TAXED_ICMS_CSTS
                    else None
                ),
                "rate": self._format_rate(icms_rate) if has_icms_rate else None,
                "value": (
                    self._format_money(icms_value)
                    if icms_value is not None
                    else None
                ),
                "operation_value": (
                    self._format_money(icms_operation_value)
                    if icms_operation_value is not None
                    else None
                ),
                "deferment_rate": (
                    self._format_rate(deferment_rate)
                    if icms_cst == "51" and deferment_rate
                    else None
                ),
                "deferred_value": (
                    self._format_money(icms_deferred_value)
                    if icms_deferred_value is not None
                    else None
                ),
                "tax_treatment_confirmed": (
                    tax_treatment_confirmed
                    if icms_cst
                    in self.NON_TAXED_ICMS_CSTS | self.REVIEWABLE_ICMS_CSTS
                    else None
                ),
                "diagnostic_only": (
                    (icms_cst == "51" and not has_icms_rate)
                    or (
                        icms_cst in self.NON_TAXED_ICMS_CSTS
                        and not tax_treatment_confirmed
                    )
                    or (
                        icms_cst in self.REVIEWABLE_ICMS_CSTS
                        and not tax_treatment_confirmed
                    )
                ),
                "st_base_method": st_base_method,
                "st_mva_rate": (
                    self._format_rate(st_mva_rate)
                    if icms_cst in self.ST_ICMS_CSTS
                    and st_base_method == "4"
                    else None
                ),
                "st_base_reduction_rate": (
                    self._format_rate(st_base_reduction_rate)
                    if icms_cst in self.ST_ICMS_CSTS
                    and has_st_base_reduction
                    else None
                ),
                "st_base": self._format_money(st_base),
                "st_rate": (
                    self._format_rate(st_rate)
                    if icms_cst in self.ST_ICMS_CSTS
                    else "0.0000"
                ),
                "st_value": self._format_money(st_value),
                "retained_st_base": (
                    self._format_money(retained_st_base)
                    if icms_cst in self.RETAINED_ST_ICMS_CSTS
                    else None
                ),
                "retained_st_rate": (
                    self._format_rate(retained_st_rate)
                    if icms_cst in self.RETAINED_ST_ICMS_CSTS
                    else None
                ),
                "retained_st_value": (
                    self._format_money(retained_st_value)
                    if icms_cst in self.RETAINED_ST_ICMS_CSTS
                    else None
                ),
                "duimp_value": (
                    source_icms.get("duimp_value")
                    if source_icms.get("duimp_value") not in (None, "")
                    else source_icms.get("value")
                ),
                "calculation_source": (
                    source_icms.get("calculation_source") or "tax_rule"
                ),
            }
            if taxes["icms"].get("duimp_value") not in (None, ""):
                taxes["icms"]["difference"] = self._format_money(
                    self._money(taxes["icms"].get("value"))
                    - self._money(taxes["icms"]["duimp_value"])
                )
            benefit_code = item.get("benefit_code") or configuration.get(
                "icms_benefit_code"
            )
            if benefit_code:
                item["benefit_code"] = str(benefit_code)
                taxes["icms"]["benefit_code"] = str(benefit_code)
            ipi_cst = str(configuration.get("ipi_cst") or "49").zfill(2)
            if ipi["rate"] == 0 and ipi["value"] == 0:
                ipi_cst = str(
                    configuration.get("ipi_zero_rate_cst") or "01"
                ).zfill(2)
            taxes["ipi"] = {
                **taxes.get("ipi", {}),
                "cst": ipi_cst,
                "enquiry_code": str(configuration.get("ipi_enquiry_code") or "999"),
                "base": self._format_money(ipi["base"]),
                "rate": self._format_rate(ipi["rate"]),
                "value": self._format_money(ipi["value"]),
            }
            taxes["ii"] = {
                **taxes.get("ii", {}),
                "base": self._format_money(ii["base"] or product_value),
                "customs_expenses": self._format_money(ii["customs_expenses"]),
                "value": self._format_money(ii["value"]),
                "iof": self._format_money(ii["iof"]),
            }
            taxes["pis"] = {
                **taxes.get("pis", {}),
                "cst": str(configuration.get("pis_cst") or "98").zfill(2),
                "base": self._format_money(pis["base"] or product_value),
                "rate": self._format_rate(pis["rate"]),
                "value": self._format_money(pis["value"]),
            }
            taxes["cofins"] = {
                **taxes.get("cofins", {}),
                "cst": str(configuration.get("cofins_cst") or "98").zfill(2),
                "base": self._format_money(cofins["base"] or product_value),
                "rate": self._format_rate(cofins["rate"]),
                "value": self._format_money(cofins["value"]),
            }

            classification = (
                item.get("tax_classification_code")
                or configuration.get("tax_classification_code")
            )
            if classification:
                ibs_uf_rate = self._decimal(configuration.get("ibs_uf_rate"))
                ibs_mun_rate = self._decimal(configuration.get("ibs_mun_rate"))
                cbs_rate = self._decimal(configuration.get("cbs_rate"))
                ibs_cbs_base = self._money(
                    product_value
                    + freight_value
                    + insurance_value
                    + ii["value"]
                    + item_other
                    - (icms_value or Decimal("0"))
                    - pis["value"]
                    - cofins["value"]
                    - discount
                )
                ibs_uf_value = self._money(
                    ibs_cbs_base * ibs_uf_rate / Decimal("100")
                )
                ibs_mun_value = self._money(
                    ibs_cbs_base * ibs_mun_rate / Decimal("100")
                )
                cbs_value = self._money(ibs_cbs_base * cbs_rate / Decimal("100"))
                taxes["ibs_cbs"] = {
                    "cst": str(configuration.get("ibs_cbs_cst") or "000").zfill(3),
                    "classification": str(classification).zfill(6),
                    "base": self._format_money(ibs_cbs_base),
                    "ibs_uf_rate": self._format_rate(ibs_uf_rate),
                    "ibs_uf_value": self._format_money(ibs_uf_value),
                    "ibs_mun_rate": self._format_rate(ibs_mun_rate),
                    "ibs_mun_value": self._format_money(ibs_mun_value),
                    "ibs_value": self._format_money(ibs_uf_value + ibs_mun_value),
                    "cbs_rate": self._format_rate(cbs_rate),
                    "cbs_value": self._format_money(cbs_value),
                }

            import_payload = deepcopy(item.get("import_payload") or {})
            import_payload["afrmm_value"] = self._format_money(item_afrmm)
            item["import_payload"] = import_payload
            item["tax_payload"] = taxes
            calculated_items.append(item)

        return calculated_items, self.calculate_totals(calculated_items)

    def calculate_totals(self, items: list[dict[str, Any]]) -> dict[str, str]:
        fields = {
            "products_value": Decimal("0"),
            "freight_value": Decimal("0"),
            "insurance_value": Decimal("0"),
            "discount_value": Decimal("0"),
            "other_value": Decimal("0"),
            "ii_value": Decimal("0"),
            "ipi_value": Decimal("0"),
            "pis_value": Decimal("0"),
            "cofins_value": Decimal("0"),
            "icms_base": Decimal("0"),
            "icms_value": Decimal("0"),
            "icms_st_base": Decimal("0"),
            "icms_st_value": Decimal("0"),
            "ibs_cbs_base": Decimal("0"),
            "ibs_uf_value": Decimal("0"),
            "ibs_mun_value": Decimal("0"),
            "ibs_value": Decimal("0"),
            "cbs_value": Decimal("0"),
            "afrmm_value": Decimal("0"),
            "siscomex_fee": Decimal("0"),
            "thc_value": Decimal("0"),
            "additional_other_value": Decimal("0"),
        }
        for item in items:
            fields["products_value"] += self._money(item.get("product_value"))
            fields["freight_value"] += self._money(item.get("freight_value"))
            fields["insurance_value"] += self._money(item.get("insurance_value"))
            fields["discount_value"] += self._money(item.get("discount_value"))
            fields["other_value"] += self._money(item.get("other_value"))
            import_payload = item.get("import_payload") or {}
            fields["afrmm_value"] += self._money(
                import_payload.get("afrmm_value")
            )
            cost_allocation = item.get("cost_allocation") or {}
            fields["siscomex_fee"] += self._money(
                cost_allocation.get("siscomex_fee")
            )
            fields["thc_value"] += self._money(cost_allocation.get("thc"))
            fields["additional_other_value"] += self._money(
                cost_allocation.get("other")
            )
            taxes = item.get("tax_payload") or {}
            for tax, field in {
                "ii": "ii_value",
                "ipi": "ipi_value",
                "pis": "pis_value",
                "cofins": "cofins_value",
                "icms": "icms_value",
            }.items():
                fields[field] += self._money((taxes.get(tax) or {}).get("value"))
            fields["icms_base"] += self._money(
                (taxes.get("icms") or {}).get("base")
            )
            fields["icms_st_base"] += self._money(
                (taxes.get("icms") or {}).get("st_base")
            )
            fields["icms_st_value"] += self._money(
                (taxes.get("icms") or {}).get("st_value")
            )
            ibs_cbs = taxes.get("ibs_cbs") or {}
            for key in [
                "ibs_cbs_base",
                "ibs_uf_value",
                "ibs_mun_value",
                "ibs_value",
                "cbs_value",
            ]:
                source = "base" if key == "ibs_cbs_base" else key.removeprefix("ibs_")
                if key == "ibs_uf_value":
                    source = "ibs_uf_value"
                elif key == "ibs_mun_value":
                    source = "ibs_mun_value"
                elif key == "ibs_value":
                    source = "ibs_value"
                elif key == "cbs_value":
                    source = "cbs_value"
                fields[key] += self._money(ibs_cbs.get(source))

        invoice_value = (
            fields["products_value"]
            + fields["freight_value"]
            + fields["insurance_value"]
            + fields["other_value"]
            + fields["ii_value"]
            + fields["ipi_value"]
            + fields["pis_value"]
            + fields["cofins_value"]
            + fields["icms_value"]
            + fields["icms_st_value"]
            - fields["discount_value"]
        )
        rtc_invoice_value = (
            fields["products_value"]
            + fields["freight_value"]
            + fields["insurance_value"]
            + fields["other_value"]
            + fields["ii_value"]
            + fields["ipi_value"]
            + fields["icms_st_value"]
            - fields["discount_value"]
        )
        fields["invoice_value"] = invoice_value
        fields["rtc_invoice_value"] = rtc_invoice_value
        return {key: self._format_money(value) for key, value in fields.items()}

    def allocate(self, total: Decimal, weights: list[Decimal]) -> list[Decimal]:
        total = self._money(total)
        if not weights:
            return []
        weight_total = sum(weights, Decimal("0"))
        if total == 0:
            return [Decimal("0.00") for _ in weights]
        if weight_total <= 0:
            raise ImportTaxCalculationError(
                "Não é possível ratear despesas sem valores de produto positivos."
            )

        total_cents = int(total / self.MONEY)
        exact_shares = [
            Decimal(total_cents) * weight / weight_total for weight in weights
        ]
        allocation_cents = [
            int(share.quantize(Decimal("1"), rounding=ROUND_DOWN))
            for share in exact_shares
        ]
        remainder = total_cents - sum(allocation_cents)
        remainder_order = sorted(
            range(len(weights)),
            key=lambda index: (
                exact_shares[index] - Decimal(allocation_cents[index]),
                -index,
            ),
            reverse=True,
        )
        for index in remainder_order[:remainder]:
            allocation_cents[index] += 1
        allocations = [
            Decimal(cents) * self.MONEY for cents in allocation_cents
        ]
        return allocations

    def reconcile(
        self,
        items: list[dict[str, Any]],
        totals: dict[str, str],
        *,
        expected_tax_totals: dict[str, Any] | None = None,
        expected_additional_costs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checks: list[dict[str, str | bool]] = []
        tolerance = self.MONEY

        for tax_name, total_field in {
            "ii": "ii_value",
            "ipi": "ipi_value",
            "pis": "pis_value",
            "cofins": "cofins_value",
            "icms": "icms_value",
        }.items():
            expected_tax = (expected_tax_totals or {}).get(tax_name)
            if expected_tax is None:
                continue
            if isinstance(expected_tax, dict):
                expected_tax = expected_tax.get("value")
            self._append_reconciliation_check(
                checks,
                name=f"duimp_{tax_name}",
                expected=expected_tax,
                calculated=totals.get(total_field),
                tolerance=tolerance,
            )
            if (
                tax_name == "icms"
                and not checks[-1]["matches"]
                and self.is_diagnostic_full_icms_reduction(items)
            ):
                checks[-1].update(
                    {
                        "blocking": False,
                        "code": "diagnostic_icms_reconciliation_difference",
                        "message": (
                            "O ICMS declarado na DUIMP diverge do vICMS da "
                            "NF-e diagnóstica com CST 51 e redução integral da "
                            "base. A divergência exige revisão fiscal, mas não "
                            "impede a geração do XML não assinado."
                        ),
                    }
                )

        for cost_name, total_field in {
            "afrmm": "afrmm_value",
            "siscomex_fee": "siscomex_fee",
            "thc": "thc_value",
            "other": "additional_other_value",
        }.items():
            self._append_reconciliation_check(
                checks,
                name=f"allocation_{cost_name}",
                expected=(expected_additional_costs or {}).get(cost_name),
                calculated=totals.get(total_field),
                tolerance=Decimal("0"),
            )

        failed = [check for check in checks if not check["matches"]]
        blocking_failed = [
            check for check in failed if check.get("blocking", True)
        ]
        warning_failed = [
            check for check in failed if not check.get("blocking", True)
        ]
        return {
            "status": "balanced" if not failed else "requires_review",
            "tolerance": self._format_money(tolerance),
            "checks": checks,
            "failed_checks": len(failed),
            "blocking_failed_checks": len(blocking_failed),
            "warning_checks": len(warning_failed),
            "item_count": len(items),
        }

    def is_diagnostic_full_icms_reduction(
        self, items: list[dict[str, Any]]
    ) -> bool:
        """Identify the controlled ICMS51 diagnostic scenario.

        This classification is deliberately strict so that unrelated ICMS
        divergences remain blocking reconciliation errors.
        """
        if not items:
            return False

        for item in items:
            icms = ((item.get("tax_payload") or {}).get("icms") or {})
            if str(icms.get("cst") or "").zfill(2) != "51":
                return False
            if icms.get("diagnostic_only") is not True:
                return False
            if self._decimal(icms.get("base_reduction_rate")) != Decimal(
                "100"
            ):
                return False
            if icms.get("rate") not in (None, ""):
                return False
            if icms.get("value") not in (None, "") and self._money(
                icms.get("value")
            ) != Decimal("0.00"):
                return False

        return True

    def _append_reconciliation_check(
        self,
        checks: list[dict[str, str | bool]],
        *,
        name: str,
        expected: Any,
        calculated: Any,
        tolerance: Decimal,
    ) -> None:
        if expected in (None, ""):
            return
        expected_value = self._money(expected)
        calculated_value = self._money(calculated)
        difference = calculated_value - expected_value
        checks.append(
            {
                "name": name,
                "expected": self._format_money(expected_value),
                "calculated": self._format_money(calculated_value),
                "difference": self._format_money(difference),
                "matches": abs(difference) <= tolerance,
                "blocking": True,
            }
        )

    def _tax(self, taxes: dict[str, Any], name: str) -> dict[str, Decimal]:
        data = taxes.get(name) or {}
        return {
            "value": self._money(data.get("value")),
            "base": self._money(data.get("base")),
            "rate": self._decimal(data.get("rate")),
            "customs_expenses": self._money(data.get("customs_expenses")),
            "iof": self._money(data.get("iof")),
        }

    def _money(self, value: Any) -> Decimal:
        return self._decimal(value).quantize(self.MONEY, rounding=ROUND_HALF_UP)

    def _format_money(self, value: Any) -> str:
        return format(self._money(value), ".2f")

    def _format_rate(self, value: Any) -> str:
        return format(
            self._decimal(value).quantize(self.RATE, rounding=ROUND_HALF_UP), ".4f"
        )

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value if value not in (None, "") else "0"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ImportTaxCalculationError(f"Valor numérico inválido: {value}") from exc
