"""What-if: what happens to coverage when the tissue culture goes wrong.

Ported from the standalone TC Scenarios / TC Planner web pages, which ran on
the older `Market Demand Plan` / `Crop Protocol` doctypes. Everything here
reads the Summer Flower model instead, so there is one backend rather than two
describing the same crop.

The chain being simulated, all of it off the protocol:

    TC arrives ─ tray + pot ─ ramp to max production ─ motherstock cutting
              └─ cuttings/week ×ratio ─ stick ─ plant ─ pinch ─ flush 1..n

Three things can go wrong, and each is a scenario:

  late        the order slips; the whole chain shifts right by that many weeks
  quantity    too few or too many plantlets; the motherstock is the wrong size
  schedule    cuttings are not taken every week, so some weeks yield nothing

Motherstock is sized on what it can cut per week, never on an annual total —
a cutting cannot be banked.
"""

from __future__ import annotations

import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt

from upande_summer_flowers.perms import require
from upande_summer_flowers.summer_flowers.planning import (
    add_weeks, current_week, iso_monday, iso_year_week,
)

MAX_SIM_WEEKS = 320


def _guard(ptype: str = "read"):
    """A signed-in session is not an access check; require the real permission."""
    require(ptype)


def protocol_params(name: str) -> dict:
    """Everything the simulation needs, read off the protocol document."""
    pr = frappe.get_cached_doc("Summer Flower Protocol", name)
    curve = [flt(f.stems_per_plant) for f in
             sorted(pr.flush_schedule, key=lambda r: cint(r.flush_number))]
    if not curve:
        frappe.throw(_("Protocol {0} has no flush schedule.").format(name))
    return {
        "name": pr.name,
        "tray": cint(pr.weeks_on_tray),
        "pot": cint(pr.weeks_on_pot),
        "max_pc": cint(pr.weeks_to_max_pc),
        "harden": cint(pr.hardening_weeks),
        # The doctype already sums these; use its answer, not our own.
        "establish": cint(pr.establishment_weeks),
        "cut_per_plant_wk": flt(pr.cuttings_per_plant_per_week) or 1.0,
        "stick_to_plant": cint(pr.sticking_to_planting_weeks),
        "to_pinch": cint(pr.weeks_to_pinch),
        "flush_interval": cint(pr.flush_interval_weeks) or 13,
        "first_harvest_offset": cint(pr.first_harvest_offset_weeks),
        "curve": curve,
        "flushes": len(curve),
        "plants_per_bed": cint(pr.plants_per_bed) or 1000,
        "ms_life": cint(pr.motherstock_life_weeks) or 52,
        "supplier_lead": cint(pr.lead_time_weeks),
        "rooting": flt(pr.rooting_success_pct) or 100.0,
        "establishment_pct": flt(pr.field_establishment_pct) or 100.0,
    }


def _ramp(p: dict) -> list[float]:
    """Fraction of full cutting capacity in each week after potting.

    The protocol carries the ramp only as a length, so it is spread evenly
    rather than invented — a straight line to full production.
    """
    n = max(1, p["max_pc"])
    return [(i + 1) / n for i in range(n)]


def simulate(demand_name: str, tc_qty: int, delay_weeks: int = 0,
             field_weeks: list[int] | None = None,
             arrival: tuple[int, int] | None = None) -> dict:
    """Run one scenario and score it against the demand register."""
    d = frappe.get_doc("Summer Flower Market Demand", demand_name)
    p = protocol_params(d.protocol)

    rows = sorted(((cint(r.year), cint(r.week_no), cint(r.demand_stems))
                   for r in d.demand_weeks if r.demand_stems),
                  key=lambda t: (t[0], t[1]))
    if not rows:
        frappe.throw(_("Demand register {0} has no weeks.").format(demand_name))
    demand = {(y, w): s for y, w, s in rows}

    # Cutting to first harvest, and TC arrival to the first cutting.
    cut_to_harvest = p["stick_to_plant"] + p["first_harvest_offset"]
    tc_to_cut = p["establish"]

    if arrival:
        arr = arrival
    else:
        # Default: land the TC so the first cutting serves the first demand week.
        arr = add_weeks(rows[0][0], rows[0][1], -(tc_to_cut + cut_to_harvest))
    arr = add_weeks(arr[0], arr[1], cint(delay_weeks))

    sched = set(field_weeks) if field_weeks else None
    ramp = _ramp(p)
    survival = (p["rooting"] / 100.0) * (p["establishment_pct"] / 100.0) or 1.0

    production: dict = {}
    first_harvest = None
    beds_cut = 0

    # Motherstock generations: the first ramps up, replacements arrive
    # already producing because they were grown on before the handover.
    last_abs = rows[-1][0] * 53 + rows[-1][1]
    for sw in range(MAX_SIM_WEEKS):
        ms_wk = sw - p["establish"]           # weeks since this stock could cut
        if ms_wk < 0:
            continue
        gen = ms_wk // p["ms_life"]
        in_gen = ms_wk % p["ms_life"]
        ratio = 1.0 if gen else (ramp[in_gen] if in_gen < len(ramp) else 1.0)

        cut_y, cut_w = add_weeks(arr[0], arr[1], sw)
        if sched is not None and cut_w not in sched:
            continue
        cuttings = int(tc_qty * p["cut_per_plant_wk"] * ratio * survival)
        if cuttings <= 0:
            continue
        # Cumulative over the whole simulated run, so this is every bed ever
        # stuck, not the standing area. Named accordingly -- reported as beds
        # it read ~3.7x the plan's standing figure and invited the comparison.
        beds_cut += cuttings / p["plants_per_bed"]

        for i, spp in enumerate(p["curve"]):
            hy, hw = add_weeks(cut_y, cut_w,
                               cut_to_harvest + i * p["flush_interval"])
            if hy * 53 + hw > last_abs + 53:
                break
            production[(hy, hw)] = production.get((hy, hw), 0) + int(cuttings * spp)
            if first_harvest is None or (hy, hw) < first_harvest:
                first_harvest = (hy, hw)

        if arr[0] * 53 + arr[1] + sw > last_abs:
            break

    weeks, met, total, short_wks, unmet = [], 0, 0, 0, 0
    for y, w, want in rows:
        got = production.get((y, w), 0)
        total += want
        met += min(got, want)
        if got < want:
            short_wks += 1
            unmet += want - got
        weeks.append({"year": y, "week": w, "demand": want, "production": got,
                      "label": f"W{w}/{str(y)[2:]}"})

    order = add_weeks(arr[0], arr[1], -p["supplier_lead"])
    ready = add_weeks(arr[0], arr[1], p["establish"])
    return {
        "tc_qty": int(tc_qty),
        "delay_weeks": cint(delay_weeks),
        "field_weeks": len(sched) if sched is not None else 52,
        "arrival": {"year": arr[0], "week": arr[1], "date": str(iso_monday(*arr))},
        "order_by": {"year": order[0], "week": order[1], "date": str(iso_monday(*order))},
        "ms_ready": {"year": ready[0], "week": ready[1]},
        "first_harvest": ({"year": first_harvest[0], "week": first_harvest[1]}
                          if first_harvest else None),
        "coverage_pct": round(met / total * 100, 1) if total else 0,
        "total_demand": total, "total_met": met,
        "shortfall_weeks": short_wks, "unmet_stems": unmet,
        "beds_cut_total": round(beds_cut, 1),
        "weeks": weeks,
        "lead": {"supplier": p["supplier_lead"], "establish": p["establish"],
                 "cut_to_harvest": cut_to_harvest,
                 "total": p["supplier_lead"] + p["establish"] + cut_to_harvest},
    }


def baseline_qty(demand_name: str, target_pct: float = 95.0) -> int:
    """Smallest tissue-culture order that covers `target_pct` of the register.

    A binary search rather than a formula: coverage is not linear in plantlets
    once whole flushes and a ramp are involved.
    """
    lo, hi, best = 100, 60000, 60000
    for _i in range(15):
        mid = max(100, round((lo + hi) / 2 / 100) * 100)
        if simulate(demand_name, mid)["coverage_pct"] < target_pct:
            lo = mid
        else:
            hi = mid
            best = mid
        if hi - lo <= 100:
            break
    return best


@frappe.whitelist()
def registers() -> dict:
    """Demand registers that can be simulated, with their protocol."""
    _guard()
    return {"registers": frappe.get_all(
        "Summer Flower Market Demand",
        fields=["name", "variety", "farm", "protocol", "weeks_covered",
                "total_demand_stems"],
        order_by="farm asc, variety asc")}


@frappe.whitelist()
def run(demand: str, scenario: str = "baseline", tc_qty=None, qty_pct: int = 100,
        delay_weeks: int = 0, field_weeks=None, compare: int = 1) -> dict:
    """One scenario, plus the fixed set every planner wants to see beside it."""
    _guard()
    base = cint(tc_qty) or baseline_qty(demand)

    if isinstance(field_weeks, str):
        field_weeks = json.loads(field_weeks or "null")
    fw = [cint(x) for x in field_weeks] if field_weeks else None

    qty = base
    delay = 0
    sched = None
    if scenario == "late":
        delay = cint(delay_weeks)
    elif scenario == "quantity":
        qty = max(100, int(base * cint(qty_pct) / 100))
    elif scenario == "schedule":
        sched = fw

    out = {"baseline_qty": base,
           "result": simulate(demand, qty, delay, sched),
           "scenario": scenario,
           "plan": plan_reference(demand)}

    if cint(compare):
        # The same handful of what-ifs every time, so a change can be judged
        # against something rather than admired on its own.
        out["compare"] = [
            {"label": "Baseline", **_score(demand, base)},
            {"label": "4 weeks late", **_score(demand, base, delay=4)},
            {"label": "13 weeks late", **_score(demand, base, delay=13)},
            {"label": "Half the TC", **_score(demand, max(100, base // 2))},
            {"label": "Half again", **_score(demand, int(base * 1.5))},
            {"label": "Cut 26 weeks a year",
             **_score(demand, base, sched=list(range(1, 27)))},
        ]
    return out


def _score(demand, qty, delay=0, sched=None) -> dict:
    r = simulate(demand, qty, delay, sched)
    return {"tc_qty": r["tc_qty"], "coverage_pct": r["coverage_pct"],
            "shortfall_weeks": r["shortfall_weeks"], "unmet_stems": r["unmet_stems"],
            "first_harvest": r["first_harvest"], "field_weeks": r["field_weeks"]}


def plan_reference(demand_name: str) -> dict | None:
    """What the committed Production Plan says for the same register.

    Two engines model this crop: the author's `_populate`, which builds the
    real plan and is the authority, and `simulate` here, which answers
    what-if questions the plan cannot. They will not always agree — the plan
    sizes plantings week by week and keeps them; the simulation runs one
    motherstock at a steady rate. Where they diverge the page must say so,
    because a planner comparing two coverage figures with no explanation will
    rightly trust neither.
    """
    rows = frappe.get_all(
        "Summer Flower Production Plan",
        filters={"market_demand": demand_name, "docstatus": ("<", 2)},
        fields=["name", "status", "docstatus", "coverage_pct",
                "total_production_stems", "weeks_in_deficit", "new_beds_required"],
        order_by="docstatus desc, creation desc", limit=1)
    return rows[0] if rows else None
