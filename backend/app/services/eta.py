"""Lane transit model (assumption A6): empirical quantiles per origin->destination state with fallbacks."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.policy_config import AssumptionParams
from app.domain.models import LaneStat, LineRecord, SellerStat

QUANTILE_PS: list[float] = [i / 20 for i in range(21)]


class LaneBook:
    def __init__(self, lanes: list[LaneStat], min_samples: int):
        self.min_samples = min_samples
        self.by_lane: dict[tuple[str, str], LaneStat] = {}
        self.by_dest: dict[str, LaneStat] = {}
        self.national: LaneStat | None = None
        for ls in lanes:
            if ls.level == "lane":
                self.by_lane[(ls.origin, ls.dest)] = ls
            elif ls.level == "destination":
                self.by_dest[ls.dest] = ls
            elif ls.level == "national":
                self.national = ls
        if self.national is None:
            raise ValueError("lane statistics are missing the national fallback")

    def get(self, origin: str, dest: str) -> LaneStat:
        ls = self.by_lane.get((origin, dest))
        if ls is not None and ls.n >= self.min_samples:
            return ls
        ls = self.by_dest.get(dest)
        if ls is not None and ls.n >= self.min_samples:
            return ls
        assert self.national is not None
        return self.national


def cdf(lane: LaneStat, x_days: float) -> float:
    """P(transit <= x) by linear interpolation over the stored quantiles."""
    q = lane.quantiles
    if x_days < q[0]:
        return 0.0
    if x_days >= q[-1]:
        return 1.0
    i = 0
    for j in range(len(q) - 1):
        if q[j] <= x_days:
            i = j
    lo, hi = q[i], q[i + 1]
    if hi <= lo:
        return QUANTILE_PS[i + 1]
    frac = (x_days - lo) / (hi - lo)
    return QUANTILE_PS[i] + frac * (QUANTILE_PS[i + 1] - QUANTILE_PS[i])


def prob_exceeds(lane: LaneStat, x_days: float) -> float:
    """P(transit > x). Returns 1.0 when no time is left."""
    if x_days <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - cdf(lane, x_days)))


def prob_exceeds_given(lane: LaneStat, x_days: float, elapsed_days: float) -> float:
    """P(transit > x | transit > elapsed): an in-transit shipment that has already been on the road for
    `elapsed_days` without arriving has a different outlook than a fresh one."""
    if elapsed_days <= 0:
        return prob_exceeds(lane, x_days)
    if x_days <= elapsed_days:
        return 1.0  # the time available has already run out while the shipment is still travelling
    survive = 1.0 - cdf(lane, elapsed_days)
    if survive <= 1e-9:
        return 1.0  # older than anything seen on this lane: treat as at risk
    return max(0.0, min(1.0, (1.0 - cdf(lane, x_days)) / survive))


def days_between(a: datetime, b: datetime) -> float:
    """b - a in fractional days."""
    return (b - a).total_seconds() / 86400.0


def handover_expected(
    line: LineRecord,
    as_of: datetime,
    seller_stats: dict[str, SellerStat],
    global_handling_days: float,
    global_approval_days: float,
) -> datetime:
    """When the seller hands the item to the carrier (actual for in-transit lines)."""
    if line.stage == "in_transit" and line.carrier_ts is not None:
        return line.carrier_ts
    approved = line.approved_ts or (line.purchase_ts + timedelta(days=global_approval_days))
    stat = seller_stats.get(line.seller_id)
    handling = (
        stat.median_handling_days
        if stat is not None and stat.median_handling_days is not None
        else global_handling_days
    )
    return max(as_of, approved + timedelta(days=handling))


def baseline_eta(handover: datetime, lane: LaneStat, as_of: datetime, quantile: str = "p50") -> datetime:
    days = lane.p50 if quantile == "p50" else lane.p90
    eta = handover + timedelta(days=days)
    floor = as_of + timedelta(days=0.5)  # an overdue in-transit shipment still needs time to arrive
    return max(eta, floor)


def billable_weight_kg(weight_g: float, length_cm: float, height_cm: float, width_cm: float, divisor: float) -> float:
    return max(weight_g / 1000.0, (length_cm * height_cm * width_cm) / divisor)


PROFILE_RANK = {"PARCEL": 0, "MEDIUM": 1, "BULKY": 2}


def handling_profile(billable_kg: float, length_cm: float, height_cm: float, width_cm: float, p: AssumptionParams) -> str:
    dims = (length_cm, height_cm, width_cm)
    if billable_kg > p.medium_max_kg or max(dims) > p.bulky_dim_cm or sum(dims) > p.bulky_sum_dims_cm:
        return "BULKY"
    if billable_kg <= p.parcel_max_kg and max(dims) <= p.parcel_max_dim_cm:
        return "PARCEL"
    return "MEDIUM"
