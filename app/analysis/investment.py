from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class InvestmentMetrics:
    purchase_price: float | None
    purchase_price_source: str | None
    estimated_monthly_rent: float | None
    annual_gross_income: float | None
    vacancy_rate_used: float | None
    vacancy_loss_annual: float | None
    effective_gross_income: float | None
    opex_monthly_used: float | None
    opex_ratio_used: float | None
    opex_annual: float | None
    noi_annual: float | None
    capex_initial_used: float | None
    total_initial_outlay: float | None
    gross_yield_on_price: float | None
    gross_yield_on_total_outlay: float | None
    noi_yield_on_price: float | None
    noi_yield_on_total_outlay: float | None
    payback_years: float | None
    assumptions: dict[str, Any] = field(default_factory=dict)
    missing_assumptions: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_investment_metrics(
    *,
    purchase_price: float | None,
    purchase_price_source: str | None,
    estimated_monthly_rent: float | None,
    opex_ratio: float | None = None,
    opex_monthly: float | None = None,
    vacancy_rate: float | None = None,
    capex_initial: float | None = None,
    min_gross_yield: float | None = None,
    min_noi_yield: float | None = None,
    max_payback_years: float | None = None,
) -> InvestmentMetrics:
    flags: list[str] = []
    missing: list[str] = []
    assumptions = {
        "min_gross_yield": min_gross_yield,
        "min_noi_yield": min_noi_yield,
        "max_payback_years": max_payback_years,
    }
    if purchase_price is None:
        missing.append("investment_purchase_price")
        flags.append("missing_investment_purchase_price")
    elif purchase_price <= 0:
        flags.append("invalid_purchase_price")
        purchase_price = None
    if estimated_monthly_rent is None:
        missing.append("estimated_monthly_rent")
        flags.append("missing_estimated_monthly_rent")
    elif estimated_monthly_rent <= 0:
        flags.append("invalid_estimated_monthly_rent")
        estimated_monthly_rent = None
    if vacancy_rate is None:
        missing.append("vacancy_rate")
        flags.append("vacancy_rate_missing_assumed_zero")
        vacancy_rate_used = 0.0
    elif 0 <= vacancy_rate <= 1:
        vacancy_rate_used = vacancy_rate
    else:
        flags.append("invalid_vacancy_rate")
        vacancy_rate_used = 0.0
    if capex_initial is None:
        missing.append("capex_initial")
        flags.append("capex_missing_assumed_zero")
        capex_initial_used = 0.0
    elif capex_initial >= 0:
        capex_initial_used = capex_initial
    else:
        flags.append("invalid_capex_initial")
        capex_initial_used = 0.0
    if opex_monthly is not None and opex_monthly < 0:
        flags.append("invalid_opex_monthly")
        opex_monthly = None
    if opex_ratio is not None and not 0 <= opex_ratio <= 1:
        flags.append("invalid_opex_ratio")
        opex_ratio = None

    annual = vacancy_loss = effective = total_outlay = gross_price = gross_total = None
    if purchase_price is not None and estimated_monthly_rent is not None:
        annual = _money(estimated_monthly_rent * 12)
        vacancy_loss = _money(annual * vacancy_rate_used)
        effective = _money(annual - vacancy_loss)
        total_outlay = _money(purchase_price + capex_initial_used)
        gross_price = _yield(annual / purchase_price)
        gross_total = _yield(annual / total_outlay) if total_outlay > 0 else None

    opex_annual = noi = noi_price = noi_total = payback = None
    opex_monthly_used = None
    opex_ratio_used = None
    if annual is not None and effective is not None:
        if opex_monthly is not None:
            opex_monthly_used = opex_monthly
            opex_annual = _money(opex_monthly * 12)
        elif opex_ratio is not None:
            opex_ratio_used = opex_ratio
            opex_annual = _money(effective * opex_ratio)
        else:
            missing.append("opex")
            flags.append("opex_missing")
        if opex_annual is not None:
            noi = _money(effective - opex_annual)
            if noi <= 0:
                flags.append("negative_or_zero_noi")
            elif purchase_price is not None and total_outlay is not None:
                noi_price = _yield(noi / purchase_price)
                noi_total = _yield(noi / total_outlay) if total_outlay > 0 else None
                payback = _years(total_outlay / noi)

    return InvestmentMetrics(
        purchase_price=_money(purchase_price),
        purchase_price_source=purchase_price_source,
        estimated_monthly_rent=_money(estimated_monthly_rent),
        annual_gross_income=annual,
        vacancy_rate_used=_yield(vacancy_rate_used),
        vacancy_loss_annual=vacancy_loss,
        effective_gross_income=effective,
        opex_monthly_used=_money(opex_monthly_used),
        opex_ratio_used=_yield(opex_ratio_used),
        opex_annual=opex_annual,
        noi_annual=noi,
        capex_initial_used=_money(capex_initial_used),
        total_initial_outlay=total_outlay,
        gross_yield_on_price=gross_price,
        gross_yield_on_total_outlay=gross_total,
        noi_yield_on_price=noi_price,
        noi_yield_on_total_outlay=noi_total,
        payback_years=payback,
        assumptions={k: v for k, v in assumptions.items() if v is not None},
        missing_assumptions=list(dict.fromkeys(missing)),
        flags=list(dict.fromkeys(flags)),
    )


def _money(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _yield(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _years(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)
