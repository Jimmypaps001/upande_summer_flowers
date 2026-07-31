"""The motherstock planner: capacity, renewal and the dates that bind them.

Ported from the standalone TC Planner web page onto the Summer Flower model.
A motherstock batch is not a static number of plants — it ramps, it produces
for a fixed life, and it has to be replaced by a generation ordered long
before the old one dies. Three questions follow, and this answers all three:

  capacity   how many cuttings can be taken in a given week
  renewal    when the replacement must be ordered so there is no gap
  urgency    how long is left before that order is late

Every figure is read off the batch and its protocol. Nothing is re-derived
that the documents already compute on save.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.perms import require
from upande_summer_flowers.summer_flowers.planning import (
    add_weeks, current_week, iso_monday, iso_year_week,
)


def _guard(ptype: str = "read"):
    """A signed-in session is not an access check; require the real permission."""
    require(ptype)


def _urgency(days: int | None) -> dict:
    """How much rope is left before the renewal order is late."""
    if days is None:
        return {"level": "none", "label": "—"}
    if days < 0:
        return {"level": "hot", "label": f"{-days}d overdue"}
    if days < 90:
        return {"level": "hot", "label": f"{days}d — order now"}
    if days < 182:
        return {"level": "warn", "label": f"{days}d"}
    return {"level": "ok", "label": f"{round(days / 7)}w away"}


def renewal_chain(batch, protocol, cycles: int = 6) -> list[dict]:
    """Every generation after this one, and when each must be ordered.

    A replacement has to be *ready* the week the current stock expires, so it
    is ordered a full establishment plus the supplier's lead before that.
    """
    life = cint(protocol.motherstock_life_weeks) or 52
    lead = cint(protocol.lead_time_weeks) + cint(protocol.establishment_weeks)
    today = getdate(nowdate())

    expiry = batch.get("expiry_date")
    if not expiry:
        return []
    out, exp = [], getdate(expiry)
    for gen in range(1, cycles + 1):
        order_by = add_days(exp, -lead * 7)
        days = (order_by - today).days
        nxt = add_days(exp, life * 7)
        out.append({
            "generation": cint(batch.get("generation") or 1) + gen,
            "order_by": str(order_by),
            "order_week": "W{}/{}".format(*reversed(iso_year_week(order_by))),
            "ready": str(exp), "expires": str(nxt),
            "days_to_order": days, "urgency": _urgency(days),
            "tc_plants": cint(batch.get("tc_plants_required")),
            "lead_weeks": lead,
        })
        exp = nxt
    return out


def capacity_weeks(batch, protocol, weeks: int = 52) -> list[dict]:
    """Cuttings available per week, and the events that change that.

    The first generation ramps to full production; a replacement is grown on
    before handover, so it starts at full. Both facts come from the protocol,
    not from an assumption here.
    """
    est = cint(protocol.establishment_weeks)
    ramp = max(1, cint(protocol.weeks_to_max_pc))
    life = cint(protocol.motherstock_life_weeks) or 52
    per_plant = flt(protocol.cuttings_per_plant_per_week) or 1.0
    mothers = cint(batch.get("mother_plants"))

    start = batch.get("tc_on_farm_date") or batch.get("tc_order_date")
    if not start or not mothers:
        return []
    sy, sw = iso_year_week(getdate(start))
    ny, nw = current_week()
    nowk = ny * 53 + nw

    out = []
    for i in range(weeks):
        y, w = add_weeks(sy, sw, i)
        ms_wk = i - est
        if ms_wk < 0:
            state, ratio = "establishing", 0.0
        else:
            gen, into = ms_wk // life, ms_wk % life
            if gen == 0 and into < ramp:
                state, ratio = "ramping", (into + 1) / ramp
            elif into == 0 and gen:
                state, ratio = "handover", 1.0
            elif into >= life - 1:
                state, ratio = "expiring", 1.0
            else:
                state, ratio = "producing", 1.0
        out.append({
            "year": y, "week": w, "label": f"W{w}/{str(y)[2:]}",
            "state": state, "ratio": round(ratio, 3),
            "cuttings": int(mothers * per_plant * ratio),
            "past": (y * 53 + w) < nowk,
        })
    return out


@frappe.whitelist()
def overview(farm: str | None = None, variety: str | None = None) -> dict:
    """Every batch with its renewal urgency, plus programme totals."""
    _guard()
    f = {}
    if farm:
        f["farm"] = farm
    if variety:
        f["variety"] = variety
    rows = frappe.get_all(
        "Summer Flower Motherstock Batch", filters=f,
        fields=["name", "variety", "farm", "protocol", "batch_status", "generation",
                "mother_plants", "pots_required", "bench_sqm", "peak_bench_sqm",
                "tc_plants_required", "build_up_cycles", "peak_weekly_cuttings",
                "tc_order_date", "tc_on_farm_date", "max_pc_date", "expiry_date",
                "renewal_tc_order_date", "total_cost", "tc_cost", "holding_cost",
                "rate_per_plantlet", "tc_stage", "schedule_warning", "production_plan"],
        order_by="expiry_date asc")

    today = getdate(nowdate())
    for b in rows:
        d = ((getdate(b.renewal_tc_order_date) - today).days
             if b.renewal_tc_order_date else None)
        b["days_to_renewal"] = d
        b["urgency"] = _urgency(d)
        pr = frappe.get_cached_doc("Summer Flower Protocol", b.protocol)
        b["weekly_capacity"] = int(cint(b.mother_plants)
                                   * (flt(pr.cuttings_per_plant_per_week) or 1))
        b["life_weeks"] = cint(pr.motherstock_life_weeks)
        b["establish_weeks"] = cint(pr.establishment_weeks)
        b["supplier_lead"] = cint(pr.lead_time_weeks)

    return {
        "batches": rows,
        "totals": {
            "batches": len(rows),
            "mothers": sum(cint(b.mother_plants) for b in rows),
            "pots": sum(cint(b.pots_required) for b in rows),
            "bench": round(sum(flt(b.peak_bench_sqm) for b in rows), 1),
            "tc_plants": sum(cint(b.tc_plants_required) for b in rows),
            "cost": round(sum(flt(b.total_cost) for b in rows), 2),
            "weekly_capacity": sum(b["weekly_capacity"] for b in rows),
            "due_soon": sum(1 for b in rows
                            if b["urgency"]["level"] in ("hot", "warn")),
        },
    }


@frappe.whitelist()
def detail(batch: str) -> dict:
    """One batch, opened up: pipeline, build-up, capacity and renewals."""
    _guard()
    b = frappe.get_doc("Summer Flower Motherstock Batch", batch)
    pr = frappe.get_cached_doc("Summer Flower Protocol", b.protocol)
    today = getdate(nowdate())

    d = (getdate(b.renewal_tc_order_date) - today).days if b.renewal_tc_order_date else None
    row = b.as_dict()
    stage = lambda label, date, sub: {
        "label": label, "date": str(date) if date else None,
        "week": "W{}/{}".format(*reversed(iso_year_week(getdate(date)))) if date else "—",
        "sub": sub, "past": bool(date and getdate(date) < today)}

    return {
        "batch": {k: row.get(k) for k in (
            "name", "variety", "farm", "protocol", "batch_status", "generation",
            "mother_plants", "pots_required", "bench_sqm", "peak_bench_sqm",
            "tc_plants_required", "build_up_cycles", "multiplication_factor",
            "peak_weekly_cuttings", "effective_peak_cuttings", "sticking_spread_weeks",
            "tc_stage", "rate_per_plantlet", "tc_cost", "holding_cost", "total_cost",
            "currency", "schedule_warning", "production_plan", "lead_time_weeks",
            "total_lead_time_weeks")},
        "urgency": _urgency(d), "days_to_renewal": d,
        "pipeline": [
            stage("Order TC", b.tc_order_date, f"{cint(pr.lead_time_weeks)}w supplier lead"),
            stage("TC on farm", b.tc_on_farm_date,
                  f"{cint(pr.establishment_weeks)}w to establish"),
            stage("Mothers at max", b.max_pc_date,
                  f"{cint(pr.motherstock_life_weeks)}w productive life"),
            stage("First sticking", b.first_sticking_date, "cuttings start"),
            stage("Expires", b.expiry_date, "replacement must be ready"),
            stage("Renew by", b.renewal_tc_order_date, "order the next generation"),
        ],
        "build_up": [{"step": s.step, "week_label": s.week_label,
                      "target_date": str(s.target_date) if s.target_date else None,
                      "mother_plants": s.mother_plants, "pots": s.pots,
                      "notes": s.notes} for s in b.build_up_steps],
        "capacity": capacity_weeks(row, pr),
        "renewals": renewal_chain(row, pr),
        "protocol": {
            "name": pr.name, "life_weeks": cint(pr.motherstock_life_weeks),
            "establish_weeks": cint(pr.establishment_weeks),
            "supplier_lead": cint(pr.lead_time_weeks),
            "ramp_weeks": cint(pr.weeks_to_max_pc),
            "cuttings_per_plant_week": flt(pr.cuttings_per_plant_per_week),
            "plants_per_pot": cint(pr.plants_per_pot),
            "pots_per_sqm": cint(pr.pots_per_sqm),
            "max_cycles": cint(pr.max_multiplication_cycles),
        },
    }
