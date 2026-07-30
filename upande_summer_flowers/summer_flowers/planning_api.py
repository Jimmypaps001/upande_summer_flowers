# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Endpoints for the planning dashboard.

Everything here is a projection: it reads saved documents but writes nothing, so
the same calls back both the current plan and a what-if slider.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from upande_summer_flowers.summer_flowers.motherstock_sim import (
	params_from_version,
	rounds_that_fit,
	simulate,
	tc_needed_for,
)
from upande_summer_flowers.summer_flowers.planning import iso_monday, iso_year_week


def _guard():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)


@frappe.whitelist()
def scope():
	"""Varieties and farms that have an active protocol version."""
	_guard()
	rows = frappe.get_all(
		"Crop Protocol Version",
		filters={"version_status": ["in", ["Active", "Superseded"]]},
		fields=["name", "variety", "farm", "version", "version_status", "is_current",
		        "effective_from"],
		order_by="variety asc, farm asc, version desc",
	)
	return {
		"versions": rows,
		"varieties": sorted({r.variety for r in rows if r.variety}),
		"farms": sorted({r.farm for r in rows if r.farm}),
	}


@frappe.whitelist()
def demand_vs_production(variety=None, farm=None, plan=None):
	"""Weekly and monthly demand against planned production, plus plants and stems.

	Answers "demand per variety against planned production, and the number of
	plants and stems" in one payload.
	"""
	_guard()
	f = {"docstatus": ["<", 2]}
	if variety:
		f["variety"] = variety
	if farm:
		f["farm"] = farm
	if not plan:
		rows = frappe.get_all("Summer Flower Production Plan", filters=f, pluck="name",
		                      order_by="creation desc", limit=1)
		if not rows:
			return {"plan": None}
		plan = rows[0]

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	weeks = [
		{
			"label": f"{w.year}-W{w.week_no:02d}",
			"year": w.year, "week_no": w.week_no, "date": str(w.week_start_date),
			"demand": w.demand_stems or 0,
			"production": w.production_stems or 0,
			"variance": w.variance_stems or 0,
			"area_ha": flt(w.area_ha),
		}
		for w in p.plan_weeks
	]
	months = [
		{
			"label": f"{m.month_name[:3]} {str(m.year)[2:]}",
			"year": m.year, "month": m.month,
			"demand": m.demand_stems or 0,
			"production": m.production_stems or 0,
			"variance": m.variance_stems or 0,
		}
		for m in p.plan_months
	]

	new_rows = [b for b in p.plan_blocks if b.is_new_planting]
	plants = sum((b.plants or 0) for b in new_rows)
	return {
		"plan": p.name,
		"variety": p.variety,
		"farm": p.farm,
		"version": p.protocol,
		"status": p.status,
		"period": f"{p.from_year}-W{p.from_week:02d} to {p.to_year}-W{p.to_week:02d}",
		"weeks": weeks,
		"months": months,
		"totals": {
			"demand_stems": p.total_demand_stems or 0,
			"production_stems": p.total_production_stems or 0,
			"variance_stems": p.total_variance_stems or 0,
			"coverage_pct": flt(p.coverage_pct),
			"weeks_in_deficit": p.weeks_in_deficit or 0,
			"plants_required": plants,
			"beds_required": p.new_beds_required or 0,
			"plantings_proposed": len(new_rows),
			"peak_weekly_sticking": p.peak_weekly_sticking or 0,
			"peak_sticking_week": p.peak_sticking_week,
			"average_area_ha": flt(p.average_area_ha),
			"stems_per_plant_life": flt(v.total_stems_per_plant_life),
			"stems_per_ha_year": flt(v.stems_per_ha_year),
		},
	}


@frappe.whitelist()
def tc_derivation(plan=None, variety=None, farm=None):
	"""Show how the TC order size falls out of demand, step by step."""
	_guard()
	dv = demand_vs_production(variety=variety, farm=farm, plan=plan)
	if not dv.get("plan"):
		return {"plan": None}

	v = frappe.get_cached_doc("Crop Protocol Version", dv["version"])
	t = dv["totals"]
	peak_plants = t["peak_weekly_sticking"] or 0
	cuttings = v.cuttings_for_plants(peak_plants) if peak_plants else 0
	per_week = flt(v.cuttings_per_plant_per_week) or 1
	mothers = int(round(cuttings / per_week)) if cuttings else 0

	steps = [
		{"step": "Peak weekly demand", "value": max((w["demand"] for w in dv["weeks"]), default=0),
		 "unit": "stems", "note": "largest single week in the plan"},
		{"step": "Stems per plant at flush 1",
		 "value": v.flush_offsets()[0][1] if v.flush_schedule else 0,
		 "unit": "stems/plant", "note": "first harvest only"},
		{"step": "Plants stuck in the peak week", "value": peak_plants, "unit": "plants",
		 "note": "this is what sizes the motherstock, not the annual total"},
		{"step": "Cuttings to stick", "value": cuttings, "unit": "cuttings",
		 "note": f"x{flt(v.cuttings_per_plant_required, 3)} for rooting and field loss, "
		         f"then {flt(v.cutting_reject_pct)}% reject"},
		{"step": "Mother plants required", "value": mothers, "unit": "plants",
		 "note": f"at {per_week:g} cutting per plant per week"},
	]
	return {
		"plan": dv["plan"], "variety": dv["variety"], "farm": dv["farm"],
		"version": v.name, "steps": steps, "mothers_required": mothers,
		"pots_required": int(round(mothers / (v.plants_per_pot or 1))) if mothers else 0,
		"bench_sqm": round(mothers / v.plants_per_sqm_bench, 1)
		if v.plants_per_sqm_bench else 0,
	}


@frappe.whitelist()
def tc_options(version, target_pool, need_by=None, field_pct=0, max_rounds=6):
	"""TC order size against how early it has to arrive.

	The trade-off the grower asked to see: more lead time means more multiplication
	rounds fit, so fewer plantlets need buying.
	"""
	_guard()
	p = params_from_version(version)
	target_pool = frappe.utils.cint(target_pool)
	need_by = getdate(need_by or nowdate())
	factor = flt(p.get("multiplication_factor_per_cycle") or 0)
	per_round = int(p.get("cycle_time_weeks") or 0) + int(p.get("weeks_to_productive_ms") or 0)
	base = int(p.get("weeks_to_productive_ms") or 0)

	out = []
	for n in range(0, frappe.utils.cint(max_rounds) + 1):
		weeks = base + n * per_round
		tc = tc_needed_for(target_pool, n, factor, field_pct)
		arrive = need_by - datetime.timedelta(weeks=weeks)
		lab = frappe.db.get_single_value("Summer Flower Settings", "lab_turnaround_weeks") or 0
		order_by = arrive - datetime.timedelta(weeks=lab)
		rate = _rate_for(tc)
		out.append({
			"rounds": n,
			"weeks_lead": weeks,
			"tc_plants": tc,
			"tc_arrival_date": str(arrive),
			"order_by_date": str(order_by),
			"order_in_past": order_by < getdate(nowdate()),
			"rate": rate,
			"cost": round(rate * tc, 2),
			"workbook_tc": int(round(target_pool / (1 + n * factor))) if (1 + n * factor) else tc,
		})
	cheapest = min((o for o in out if not o["order_in_past"] and o["cost"]),
	               key=lambda o: o["cost"], default=None)
	for o in out:
		o["is_cheapest_achievable"] = bool(cheapest and o["rounds"] == cheapest["rounds"])
	return {"target_pool": target_pool, "need_by": str(need_by), "options": out,
	        "weeks_per_round": per_round, "establishment_weeks": base}


def _rate_for(qty, stage="Stage 4", year=None):
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
		lookup_tc_rate,
	)

	return flt(lookup_tc_rate(qty, stage, year or getdate(nowdate()).year))


@frappe.whitelist()
def simulate_buildup(version, tc_plants, tc_arrival_date, rounds=4, field_pct=0,
                     horizon_weeks=160, overrides=None):
	"""Run the build-up and return the timeline, ledger and space curve.

	This is the what-if: change the TC date or amount and everything downstream
	moves with it. Nothing is written.
	"""
	_guard()
	if isinstance(overrides, str):
		overrides = frappe.parse_json(overrides)
	p = params_from_version(version, overrides)
	res = simulate(
		frappe.utils.cint(tc_plants), tc_arrival_date, p,
		rounds=frappe.utils.cint(rounds), field_pct_per_round=flt(field_pct),
		horizon_weeks=frappe.utils.cint(horizon_weeks),
	)
	rate = _rate_for(res["tc_plants"])
	res["rate"] = rate
	res["tc_cost"] = round(rate * res["tc_plants"], 2)
	res["params"] = {
		"weeks_to_field_ready": p["weeks_to_field_ready"],
		"weeks_to_productive_ms": p["weeks_to_productive_ms"],
		"cycle_time_weeks": p["cycle_time_weeks"],
		"factor": p["multiplication_factor_per_cycle"],
		"plants_per_sqm_bench": p["plants_per_sqm_bench"],
		"plants_per_bed": p["plants_per_bed"],
		"sqm_gross_per_bed": p["sqm_gross_per_bed"],
	}
	# What the released plants turn into on the ground.
	beds = (res["field_plants_released"] / p["plants_per_bed"]) if p.get("plants_per_bed") else 0
	res["field_effect"] = {
		"plants": res["field_plants_released"],
		"beds": round(beds, 1),
		"gross_area_ha": round(beds * flt(p.get("sqm_gross_per_bed")) / 10_000, 4),
		"stems_over_life": int(round(
			res["field_plants_released"] * flt(p.get("total_stems_per_plant_life"))
		)),
	}
	return res


@frappe.whitelist()
def planting_plan(plan=None, variety=None, farm=None):
	"""The planting plan: what to stick, when to plant, and where it lands.

	Also returns block occupancy windows, because a block holds one planting at a
	time and that constraint is only visible on a timeline.
	"""
	_guard()
	f = {"docstatus": ["<", 2]}
	if variety:
		f["variety"] = variety
	if farm:
		f["farm"] = farm
	if not plan:
		rows = frappe.get_all("Summer Flower Production Plan", filters=f, pluck="name",
		                      order_by="creation desc", limit=1)
		if not rows:
			return {"plan": None, "plantings": [], "occupancy": [], "totals": {}}
		plan = rows[0]

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	plantings = []
	for b in p.plan_blocks:
		plantings.append({
			"idx": b.idx,
			"is_new": bool(b.is_new_planting),
			"block": b.block,
			"existing_planting": b.existing_planting,
			"beds": b.beds or 0,
			"plants": b.plants or 0,
			"cuttings": v.cuttings_for_plants(b.plants or 0) if b.plants else 0,
			"stick": f"{b.sticking_year}-W{b.sticking_week:02d}"
			if b.sticking_year and b.sticking_week else None,
			"plant": f"{b.planting_year}-W{b.planting_week:02d}"
			if b.planting_year and b.planting_week else None,
			"planting_date": str(b.planting_date) if b.planting_date else None,
			"pinch_date": str(b.pinch_date) if b.pinch_date else None,
			"first_harvest": f"{b.first_harvest_year}-W{b.first_harvest_week:02d}"
			if b.first_harvest_year and b.first_harvest_week else None,
			"harvest_family": b.harvest_week_family,
			"gross_area_ha": flt(b.gross_area_ha),
			"lifetime_stems": b.lifetime_stems or 0,
			"below_minimum": bool(b.below_minimum),
			"in_past": bool(b.planting_in_past),
			"unallocated": bool(b.is_new_planting and not b.block),
			"notes": b.notes,
		})

	# Occupancy windows, from the plan's allocated rows plus anything already on
	# the ground as a Planting Calendar.
	life_weeks = int(v.total_weeks_in_ground or 0)
	occupancy = []
	for b in p.plan_blocks:
		if not b.block or not b.planting_date:
			continue
		start = getdate(b.planting_date)
		occupancy.append({
			"block": b.block,
			"source": "plan",
			"ref": b.existing_planting or f"row {b.idx}",
			"start": str(start),
			"end": str(start + datetime.timedelta(weeks=life_weeks)),
			"beds": b.beds or 0,
			"status": "Proposed",
		})
	for c in frappe.get_all(
		"Planting Calendar",
		filters={"calendar_status": ["not in", ("Cancelled",)]},
		fields=["name", "block", "variety", "beds", "planting_date",
		        "planned_uproot_date", "actual_uproot_date", "calendar_status"],
	):
		if variety and c.variety != variety:
			continue
		occupancy.append({
			"block": c.block,
			"source": "calendar",
			"ref": c.name,
			"start": str(c.planting_date),
			"end": str(c.actual_uproot_date or c.planned_uproot_date),
			"beds": c.beds or 0,
			"status": c.calendar_status,
		})

	new_rows = [x for x in plantings if x["is_new"]]
	blocks_available = frappe.db.count("Block", {
		"custom_is_summer_flower_block": 1, **({"farm": p.farm} if p.farm else {})
	})
	return {
		"plan": p.name,
		"variety": p.variety,
		"farm": p.farm,
		"plantings": plantings,
		"occupancy": occupancy,
		"totals": {
			"proposed": len(new_rows),
			"existing": len(plantings) - len(new_rows),
			"allocated": len([x for x in new_rows if x["block"]]),
			"unallocated": len([x for x in new_rows if x["unallocated"]]),
			"below_minimum": len([x for x in new_rows if x["below_minimum"]]),
			"in_past": len([x for x in new_rows if x["in_past"]]),
			"beds": sum(x["beds"] for x in new_rows),
			"plants": sum(x["plants"] for x in new_rows),
			"cuttings": sum(x["cuttings"] for x in new_rows),
			"blocks_available": blocks_available,
			"blocks_needed": len(new_rows),
			"weeks_in_ground": life_weeks,
			"min_planting_beds": v.min_planting_beds or 0,
		},
	}


@frappe.whitelist()
def motherstock_batches(variety=None, farm=None):
	"""Saved batches, so the dashboard can show what was actually bought and when."""
	_guard()
	f = {}
	if variety:
		f["variety"] = variety
	if farm:
		f["farm"] = farm
	return frappe.get_all(
		"Summer Flower Motherstock Batch",
		filters=f,
		fields=["name", "variety", "farm", "batch_status", "generation",
		        "tc_plants_required", "rate_per_plantlet", "tc_cost",
		        "build_up_cycles", "mother_plants", "pots_required", "bench_sqm",
		        "peak_bench_sqm", "tc_order_date", "tc_on_farm_date", "max_pc_date",
		        "expiry_date", "renewal_tc_order_date", "total_cost",
		        "peak_weekly_cuttings", "effective_peak_cuttings"],
		order_by="tc_order_date asc",
	)
