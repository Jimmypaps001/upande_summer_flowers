# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Endpoints for the planning dashboard.

Everything here is a projection: it reads saved documents but writes nothing, so
the same calls back both the current plan and a what-if slider.
"""

import datetime
import math

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
	protocol_freshness,
)
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


def resolve_plan(variety=None, farm=None, plan=None):
	"""The plan the dashboard should show when none was asked for.

	An approved plan is preferred over a newer draft. Picking simply the newest
	made the whole dashboard swing onto whatever draft was created last, which
	hid the approved plan and -- because a budget only exists once a plan is
	approved -- made the budget look as though it had disappeared.
	"""
	if plan:
		return plan
	f = {"docstatus": ["<", 2]}
	if variety:
		f["variety"] = variety
	if farm:
		f["farm"] = farm
	approved = frappe.get_all("Summer Flower Production Plan",
	                          filters={**f, "docstatus": 1}, pluck="name",
	                          order_by="creation desc", limit=1)
	if approved:
		return approved[0]
	rows = frappe.get_all("Summer Flower Production Plan", filters=f, pluck="name",
	                      order_by="creation desc", limit=1)
	return rows[0] if rows else None


@frappe.whitelist()
def plans(variety=None, farm=None):
	"""Every plan in scope, so the dashboard can offer a choice rather than guess."""
	_guard()
	f = {"docstatus": ["<", 2]}
	if variety:
		f["variety"] = variety
	if farm:
		f["farm"] = farm
	rows = frappe.get_all(
		"Summer Flower Production Plan", filters=f,
		fields=["name", "workflow_state", "status", "docstatus", "budget",
		        "market_demand", "variety", "farm", "from_year", "from_week",
		        "to_year", "to_week", "weeks_covered", "total_demand_stems",
		        "total_production_stems", "coverage_pct", "weeks_in_deficit",
		        "new_beds_required", "creation"],
		order_by="creation desc",
	)
	# Every plan carried the same period label, so eighteen of them read as
	# eighteen copies of one thing. Name the crop and the coverage instead: that is
	# what tells them apart.
	for r in rows:
		r["label"] = "%s · %s · %s%s · %.0f%% of demand" % (
			r.name, r.variety or "?", r.workflow_state or r.status or "?",
			" + budget" if r.budget else "", flt(r.coverage_pct))
		r["is_authoritative"] = r.docstatus == 1
	drafts = [r for r in rows if r.docstatus == 0]
	return {
		"plans": rows,
		"default": resolve_plan(variety, farm),
		"draft_count": len(drafts),
		"approved_count": len(rows) - len(drafts),
	}


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
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None}

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
	# The TC order is the long pole in this whole chain -- 22 to 26 weeks of lead
	# before a single cutting exists -- so the derivation carries it through to the
	# order itself rather than stopping at mother plants. The propagation plan's
	# numbers win where one exists, so the overview cannot disagree with the
	# document that actually gets approved.
	tc = {"tc_plants": 0, "tc_order_date": "", "tc_on_farm_date": "",
	      "first_sticking_date": "", "tc_source": None, "tc_cost": 0,
	      "ramp_weeks": 0, "full_capacity_date": "", "lost_to_ramp": 0}
	rows = frappe.get_all(
		"Summer Flower Propagation Plan",
		filters={"production_plan": dv["plan"], "status": ["!=", "Rejected"]},
		fields=["name", "tc_plants_required", "tc_order_date", "tc_on_farm_date",
		        "first_sticking_date", "mother_plants_required", "peak_bench_sqm",
		        "ramp_weeks", "full_capacity_date", "cuttings_lost_to_ramp",
		        "tc_cost", "status"],
		order_by="creation desc", limit=1)
	if rows:
		r = rows[0]
		tc.update({
			"tc_plants": cint(r.tc_plants_required),
			"tc_order_date": str(r.tc_order_date or ""),
			"tc_on_farm_date": str(r.tc_on_farm_date or ""),
			"first_sticking_date": str(r.first_sticking_date or ""),
			"tc_source": r.name, "tc_status": r.status,
			"tc_cost": flt(r.tc_cost),
			"ramp_weeks": cint(r.ramp_weeks),
			"full_capacity_date": str(r.full_capacity_date or ""),
			"lost_to_ramp": cint(r.cuttings_lost_to_ramp),
		})
		if cint(r.mother_plants_required):
			# The plan nets off standing motherstock and sizes on the worst uncovered
			# week, so its figure is the one to act on. Say where the difference from
			# the raw derivation comes from rather than showing two numbers.
			if cint(r.mother_plants_required) != mothers:
				steps[-1]["note"] += _(
					" — {0} says {1} after netting off standing motherstock"
				).format(r.name, cint(r.mother_plants_required))
				steps[-1]["value"] = cint(r.mother_plants_required)
			mothers = cint(r.mother_plants_required)
	elif mothers:
		# No propagation plan yet, so size the order the same way one would: run an
		# unsaved batch through its own controller instead of repeating its maths.
		probe = frappe.new_doc("Summer Flower Motherstock Batch")
		probe.variety, probe.farm, probe.protocol = dv["variety"], dv["farm"], v.name
		probe.peak_weekly_cuttings = cuttings
		probe.first_sticking_date = _first_sticking_for(dv["plan"])
		if probe.first_sticking_date:
			probe.run_method("validate")
			tc.update({
				"tc_plants": cint(probe.tc_plants_required),
				"tc_order_date": str(probe.tc_order_date or ""),
				"tc_on_farm_date": str(probe.tc_on_farm_date or ""),
				"first_sticking_date": str(probe.first_sticking_date or ""),
				"tc_source": "sized here — no propagation plan yet",
				"tc_cost": flt(probe.tc_cost),
				"ramp_weeks": cint(probe.ramp_weeks),
				"full_capacity_date": str(probe.max_pc_date or ""),
			})

	steps.append({
		"step": "TC plantlets to order", "value": tc["tc_plants"], "unit": "plantlets",
		"note": ("order by %s, on farm %s, first cut %s"
		         % (tc["tc_order_date"] or "?", tc["tc_on_farm_date"] or "?",
		            tc["first_sticking_date"] or "?")) if tc["tc_plants"]
		        else "nothing sized yet",
	})
	tc["tc_late"] = bool(tc["tc_order_date"]
	                     and getdate(tc["tc_order_date"]) < getdate(nowdate()))

	out = {
		"plan": dv["plan"], "variety": dv["variety"], "farm": dv["farm"],
		"version": v.name, "steps": steps, "mothers_required": mothers,
		"pots_required": int(round(mothers / (v.plants_per_pot or 1))) if mothers else 0,
		"bench_sqm": round(mothers / v.plants_per_sqm_bench, 1)
		if v.plants_per_sqm_bench else 0,
	}
	out.update(tc)
	return out


def _first_sticking_for(plan):
	"""Earliest sticking week the plan proposes, as a date."""
	row = frappe.db.sql("""select sticking_year y, sticking_week w
		from `tabSummer Flower Plan Block`
		where parent = %s and is_new_planting = 1 and ifnull(not_placed, 0) = 0
		  and ifnull(sticking_year, 0) > 0
		order by sticking_year asc, sticking_week asc limit 1""", (plan,), as_dict=True)
	return iso_monday(row[0].y, row[0].w) if row else None


@frappe.whitelist()
def tc_purchase(plan=None, variety=None, farm=None, tc_qty=None, tolerance_pct=10):
	"""What to buy on the first TC order, and what buying differently does.

	The recommendation is the amount that makes the pool exactly meet the plan's
	peak sticking week -- the order for a plan that works. Everything downstream is
	proportional to the pool, so the tolerance is expressed in plantlets and the
	stems follow: buy 10% under and the peak week is 10% short, which is 10% of the
	plantings that week not happening.
	"""
	_guard()
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None}

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	tol = flt(tolerance_pct) or 10.0

	peak_plants = cint(p.peak_weekly_sticking)
	cuttings = v.cuttings_for_plants(peak_plants) if peak_plants else 0
	per_week = flt(v.cuttings_per_plant_per_week) or 1.0
	mothers = int(math.ceil(cuttings / per_week)) if cuttings else 0
	cycles = cint(v.max_multiplication_cycles)
	recommended = int(math.ceil(v.tc_plants_for(mothers, cycles))) if mothers else 0

	# The propagation plan nets off standing motherstock, so where one exists its
	# order is the one to place. The recommendation above is what the plan would
	# need with no motherstock at all, which is the number to sanity-check against.
	prop = frappe.get_all(
		"Summer Flower Propagation Plan",
		filters={"production_plan": plan, "status": ["!=", "Rejected"]},
		fields=["name", "tc_plants_required", "mother_plants_required",
		        "tc_order_date", "tc_on_farm_date", "first_sticking_date",
		        "full_capacity_date", "ramp_weeks", "cuttings_uncovered",
		        "total_cuttings_required", "tc_cost", "status"],
		order_by="creation desc", limit=1)
	prop = prop[0] if prop else None

	chosen = cint(tc_qty) or (cint(prop.tc_plants_required) if prop else recommended)
	factor = 1 + (cycles * flt(v.multiplication_factor_per_cycle))
	pool_for = lambda tc: int(round(cint(tc) * factor))
	cover_of_peak = (pool_for(chosen) * per_week / cuttings * 100) if cuttings else 0
	base = cint(prop.tc_plants_required) if prop else recommended

	return {
		"plan": plan, "variety": p.variety, "farm": p.farm, "version": v.name,
		"tolerance_pct": tol,
		"peak_plants": peak_plants,
		"peak_week": p.peak_sticking_week,
		"peak_cuttings": cuttings,
		"mothers_for_peak": mothers,
		"build_up_cycles": cycles,
		"multiplication_factor": factor,
		"recommended_tc": recommended,
		"propagation_plan": prop.name if prop else None,
		"propagation_tc": cint(prop.tc_plants_required) if prop else 0,
		"propagation_status": prop.status if prop else None,
		"order_date": str(prop.tc_order_date or "") if prop else "",
		"on_farm_date": str(prop.tc_on_farm_date or "") if prop else "",
		"first_cut_date": str(prop.first_sticking_date or "") if prop else "",
		"full_capacity_date": str(prop.full_capacity_date or "") if prop else "",
		"ramp_weeks": cint(prop.ramp_weeks) if prop else len(v.ramp_ratios()),
		"chosen_tc": chosen,
		"chosen_pool": pool_for(chosen),
		"chosen_cover_pct": round(cover_of_peak, 1),
		"in_band": bool(base and abs(chosen - base) <= base * tol / 100),
		"band_low": int(round(base * (1 - tol / 100))) if base else 0,
		"band_high": int(round(base * (1 + tol / 100))) if base else 0,
		"rate_per_plantlet": flt(_rate_for(chosen)),
		"cost": round(flt(_rate_for(chosen)) * chosen, 2),
		"plan_coverage_pct": flt(p.coverage_pct),
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
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None, "plantings": [], "occupancy": [], "totals": {}}

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	life_weeks = v.total_weeks_in_ground or 0

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
			# Year and week as numbers too, so the dashboard can filter on a period
			# without parsing the label back apart.
			"planting_year": b.planting_year,
			"planting_week": b.planting_week,
			"sticking_year": b.sticking_year,
			"sticking_week": b.sticking_week,
			"planting_date": str(b.planting_date) if b.planting_date else None,
			"pinch_date": str(b.pinch_date) if b.pinch_date else None,
			# The other dates the plan commits to, so a calendar view does not have
			# to re-derive them from the protocol and risk disagreeing with the plan.
			"sticking_date": str(iso_monday(b.sticking_year, b.sticking_week))
			if b.sticking_year and b.sticking_week else None,
			"first_harvest_date": str(iso_monday(b.first_harvest_year,
			                                     b.first_harvest_week))
			if b.first_harvest_year and b.first_harvest_week else None,
			"uproot_date": str(getdate(b.planting_date)
			                   + datetime.timedelta(weeks=life_weeks))
			if b.planting_date else None,
			"first_harvest": f"{b.first_harvest_year}-W{b.first_harvest_week:02d}"
			if b.first_harvest_year and b.first_harvest_week else None,
			"harvest_family": b.harvest_week_family,
			"gross_area_ha": flt(b.gross_area_ha),
			"lifetime_stems": b.lifetime_stems or 0,
			"below_minimum": bool(b.below_minimum),
			"in_past": bool(b.planting_in_past),
			"unallocated": bool(b.is_new_planting and not b.block),
			"not_placed": bool(b.get("not_placed")),
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
	# Every tab is showing this plan's stored rows, so the page states which
	# protocol version they were built on and whether it has moved since.
	fresh = protocol_freshness(p)
	return {
		"plan": p.name,
		"variety": p.variety,
		"farm": p.farm,
		"protocol": p.protocol,
		"protocol_status": fresh["status"],
		"protocol_stale": cint(fresh["stale"]),
		"protocol_note": fresh["note"],
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
def simulate_lifecycle(version, tc_qty, order_date, num_cycles=None, to_prop_pct=0,
                       farm_overrides=None, max_bench_sqm=None, plan=None,
                       horizon_weeks=None):
	"""The weekly TC -> motherstock -> cuttings lifecycle, with propagation feedback.

	num_cycles is derived from the horizon unless a caller insists on a number: a
	generation exists because the previous one expires, and diverted cuttings add
	generations of their own. The horizon comes from the plan being looked at, so
	the run covers the period being planned and no further.
	"""
	_guard()
	from upande_summer_flowers.summer_flowers import lifecycle_sim as ls

	if isinstance(farm_overrides, str):
		farm_overrides = frappe.parse_json(farm_overrides or "{}")
	if max_bench_sqm in (None, ""):
		max_bench_sqm = frappe.db.get_single_value("Summer Flower Settings", "max_bench_sqm")

	if not horizon_weeks and plan:
		horizon_weeks = frappe.db.get_value("Summer Flower Production Plan", plan,
		                                    "weeks_covered")

	p = ls.params_from_version(version)
	res = ls.simulate(
		p, frappe.utils.cint(tc_qty), order_date,
		num_cycles=frappe.utils.cint(num_cycles) or None,
		farm_overrides=farm_overrides,
		default_to_prop_pct=flt(to_prop_pct),
		max_bench_sqm=max_bench_sqm,
		horizon_weeks=cint(horizon_weeks) or None,
	)
	# Trim the row payload: the table only needs weeks where something happens.
	res["rows"] = [
		r for r in res["rows"]
		if r["total_cap"] or r["events"] or r["phase"]
	]
	return res


@frappe.whitelist()
def block_forecast(plan=None, variety=None, farm=None, blocks=None):
	"""Per-block weekly forecast to the end of each cycle, plus uprooting dates.

	Every block at the farm is returned, including ones with nothing planted, and
	every week in a planted block's life is returned including the non-harvest
	ones. A block that is idle or between flushes should read as a real zero rather
	than be missing from the data.
	"""
	_guard()
	if isinstance(blocks, str):
		blocks = [b for b in frappe.parse_json(blocks) if b] if blocks.startswith("[") \
			else [blocks]

	bfilters = {"custom_is_summer_flower_block": 1}
	if farm:
		bfilters["farm"] = farm
	all_blocks = frappe.get_all(
		"Block", filters=bfilters,
		fields=["name", "block", "farm", "custom_total_beds", "custom_gross_area_ha"],
		order_by="block asc",
	)
	if blocks:
		all_blocks = [b for b in all_blocks if b.name in blocks]

	pfilters = {"calendar_status": ["not in", ("Cancelled",)]}
	if variety:
		pfilters["variety"] = variety
	plantings = frappe.get_all(
		"Planting Calendar", filters=pfilters,
		fields=["name", "block", "variety", "beds", "plants", "planting_date",
		        "pinch_date", "planned_uproot_date", "actual_uproot_date",
		        "calendar_status", "expected_stems_life", "crop_protocol_version"],
	)
	by_block = {}
	for pl in plantings:
		by_block.setdefault(pl.block, []).append(pl)

	# Beds for every block in scope, in one query rather than per block.
	beds_by_block = {}
	if all_blocks:
		for r in frappe.get_all(
			"Bed",
			filters={"custom_block": ["in", [b.name for b in all_blocks]]},
			fields=["name", "bed", "greenhouse", "custom_block", "custom_bed_status",
			        "custom_planting_calendar", "custom_plants", "custom_uproot_date",
			        "bed_length", "bed_width"],
			order_by="custom_block asc, bed asc",
		):
			beds_by_block.setdefault(r.custom_block, []).append({
				"bed": r.name,
				"number": r.bed,
				"status": r.custom_bed_status or "Empty",
				"planting": r.custom_planting_calendar,
				"plants": r.custom_plants or 0,
				"uproot_date": str(r.custom_uproot_date) if r.custom_uproot_date else None,
				"area_sqm": flt(r.bed_length) * flt(r.bed_width),
			})

	out = []
	for b in all_blocks:
		rows = by_block.get(b.name, [])
		beds = beds_by_block.get(b.name, [])
		# Utilisation is counted off bed records, not off Planting Calendar.beds, so a
		# bed pulled early shows as free again the moment its status changes.
		used = [x for x in beds if x["status"] in ("Planted", "Producing")]
		uprooted = [x for x in beds if x["status"] == "Uprooted"]
		free = [x for x in beds if x["status"] not in ("Planted", "Producing", "Uprooted")]
		net_total = sum(x["area_sqm"] for x in beds)
		net_used = sum(x["area_sqm"] for x in used)
		# Most Bed records carry no length/width, so area-based utilisation is not
		# trustworthy on its own. Report how much of it is actually measured and let
		# the caller fall back to bed counts rather than quote a silent under-count.
		measured = len([x for x in beds if x["area_sqm"] > 0])
		entry = {
			"block": b.name,
			"block_code": b.block,
			"farm": b.farm,
			"total_beds": b.custom_total_beds or len(beds),
			"gross_area_ha": flt(b.custom_gross_area_ha),
			"plantings": [],
			"status": "Not planted",
			"beds": beds,
			"utilisation": {
				"beds_total": len(beds),
				"beds_used": len(used),
				"beds_free": len(free),
				"beds_uprooted": len(uprooted),
				"pct_used": round(len(used) * 100.0 / len(beds), 2) if beds else 0.0,
				"net_sqm_total": round(net_total, 1),
				"net_sqm_used": round(net_used, 1),
				"net_sqm_free": round(net_total - net_used, 1),
				"beds_measured": measured,
				"area_complete": bool(beds) and measured == len(beds),
				"plants_standing": sum(x["plants"] for x in used),
			},
		}
		for pl in rows:
			flushes = frappe.get_all(
				"Planting Calendar Flush",
				filters={"parent": pl.name},
				fields=["flush_number", "harvest_date", "year", "week_no",
				        "expected_stems", "actual_stems", "is_harvested"],
				order_by="flush_number asc",
			)
			harvest_weeks = {(f.year, f.week_no): f for f in flushes}
			end = getdate(pl.actual_uproot_date or pl.planned_uproot_date)
			start = getdate(pl.planting_date)

			# Every week of the cycle, producing or not.
			weeks = []
			cur = start
			while cur <= end:
				y, w = iso_year_week(cur)
				f = harvest_weeks.get((y, w))
				weeks.append({
					"year": y, "week_no": w, "date": str(cur),
					"label": f"{y}-W{w:02d}",
					"stems": (f.actual_stems if (f and f.is_harvested) else
					          (f.expected_stems if f else 0)) or 0,
					"is_harvest_week": bool(f),
					"flush_number": f.flush_number if f else None,
					"is_actual": bool(f and f.is_harvested),
				})
				cur += datetime.timedelta(weeks=1)

			entry["plantings"].append({
				"name": pl.name, "variety": pl.variety, "beds": pl.beds,
				"plants": pl.plants, "status": pl.calendar_status,
				"planting_date": str(pl.planting_date),
				"pinch_date": str(pl.pinch_date) if pl.pinch_date else None,
				"planned_uproot_date": str(pl.planned_uproot_date)
				if pl.planned_uproot_date else None,
				"actual_uproot_date": str(pl.actual_uproot_date)
				if pl.actual_uproot_date else None,
				"uproot_date": str(end),
				"version": pl.crop_protocol_version,
				"expected_stems_life": pl.expected_stems_life or 0,
				"harvest_weeks": len(flushes),
				"cycle_weeks": len(weeks),
				"weeks": weeks,
			})
			entry["status"] = pl.calendar_status
		out.append(entry)

	# ---- the overlay: every block's weeks summed onto one series
	# Contributors are kept per week so a spike in the overlay can be traced back to
	# the blocks that caused it without re-reading the per-block payload.
	agg, contrib = {}, {}
	for b in out:
		for p in b["plantings"]:
			for w in p["weeks"]:
				key = (w["year"], w["week_no"])
				agg[key] = agg.get(key, 0) + (w["stems"] or 0)
				if w["stems"]:
					contrib.setdefault(key, []).append(
						{"block": b["block_code"], "stems": w["stems"],
						 "flush": w["flush_number"]})

	# Resolve the plan the same way every other endpoint does, so the overlay has a
	# demand line even when the caller does not know the plan name yet (the dashboard
	# loads its panes in parallel).
	plan = resolve_plan(variety, farm, plan)

	demand_map = {}
	if plan:
		pdoc = frappe.get_doc("Summer Flower Production Plan", plan)
		demand_map = {(w.year, w.week_no): (w.demand_stems or 0) for w in pdoc.plan_weeks}

	overlay = []
	for (y, w) in sorted(set(agg) | set(demand_map)):
		stems = agg.get((y, w), 0)
		dem = demand_map.get((y, w), 0)
		overlay.append({
			"year": y, "week_no": w, "label": f"{y}-W{w:02d}",
			"stems": stems, "demand": dem, "variance": stems - dem,
			"blocks_producing": len(contrib.get((y, w), [])),
			"contributors": sorted(contrib.get((y, w), []),
			                       key=lambda c: -c["stems"])[:6],
		})

	u = [b["utilisation"] for b in out]
	return {
		"blocks": out,
		"overlay": overlay,
		"totals": {
			"blocks": len(out),
			"planted": len([b for b in out if b["plantings"]]),
			"idle": len([b for b in out if not b["plantings"]]),
			"stems": sum(p["expected_stems_life"] for b in out for p in b["plantings"]),
			"beds_total": sum(x["beds_total"] for x in u),
			"beds_used": sum(x["beds_used"] for x in u),
			"beds_free": sum(x["beds_free"] for x in u),
			"beds_uprooted": sum(x["beds_uprooted"] for x in u),
			"pct_used": round(
				sum(x["beds_used"] for x in u) * 100.0 / sum(x["beds_total"] for x in u), 2
			) if sum(x["beds_total"] for x in u) else 0.0,
			"net_sqm_total": round(sum(x["net_sqm_total"] for x in u), 1),
			"net_sqm_used": round(sum(x["net_sqm_used"] for x in u), 1),
			"net_sqm_free": round(sum(x["net_sqm_free"] for x in u), 1),
			"beds_measured": sum(x["beds_measured"] for x in u),
			"blocks_area_incomplete": len([x for x in u if not x["area_complete"]]),
			"gross_area_ha": round(sum(b["gross_area_ha"] for b in out), 3),
			"plants_standing": sum(x["plants_standing"] for x in u),
			"weeks_producing": len([r for r in overlay if r["stems"]]),
			"weeks_in_deficit": len([r for r in overlay if r["variance"] < 0]),
		},
	}


EDITABLE_PROTOCOL_FIELDS = (
	"plants_per_sqm_net", "net_gross_ratio", "plants_per_bed", "beds_per_block",
	"min_planting_beds", "weeks_to_pinch", "flush_interval_weeks",
	"sticking_to_planting_weeks", "calendar_rounding_weeks", "weeks_on_tray",
	"weeks_on_pot", "weeks_to_max_pc", "hardening_weeks", "ramp_weeks",
	"ramp_profile", "supplier_lead_weeks", "weeks_to_max_production",
	"cuttings_per_plant_per_week", "plants_per_pot", "pots_per_sqm",
	"max_multiplication_cycles", "multiplication_factor_per_cycle",
	"cycle_time_weeks", "motherstock_life_weeks", "rooting_success_pct",
	"field_establishment_pct", "cutting_reject_pct", "stated_yield_stems_per_ha",
	"ready_cutting_price", "climate_note",
)


@frappe.whitelist()
def protocol_detail(version=None, variety=None, farm=None):
	"""The whole protocol sheet: inputs, derived values and the journey timelines."""
	_guard()
	if not version:
		f = {"version_status": "Active"}
		if variety:
			f["variety"] = variety
		if farm:
			f["farm"] = farm
		rows = frappe.get_all("Crop Protocol Version", filters=f, pluck="name",
		                      order_by="version desc", limit=1)
		if not rows:
			return {"version": None}
		version = rows[0]

	v = frappe.get_doc("Crop Protocol Version", version)

	flushes = []
	cum = 0
	rounding = v.calendar_rounding_weeks or 0
	# Gross, not net: 20/m² net over a 0.8 net:gross ratio is 16/m² gross, hence
	# 160,000/ha. Using net here inflates every per-hectare figure by 25%.
	plants_per_ha = (v.plants_per_sqm_gross or 0) * 10_000
	for r in sorted(v.flush_schedule, key=lambda r: r.flush_number or 0):
		cum += r.stems_per_plant or 0
		flushes.append({
			"flush": r.flush_number,
			"weeks_from_pinch": r.weeks_from_pinch,
			"weeks_from_planting": (v.weeks_to_pinch or 0) + (r.weeks_from_pinch or 0),
			"weeks_from_planting_gridded": (v.weeks_to_pinch or 0)
			+ (r.weeks_from_pinch or 0) + rounding,
			"stems_per_plant": flt(r.stems_per_plant),
			"stems_per_ha": int(round(flt(r.stems_per_plant) * plants_per_ha)),
			"cumulative_stems_per_plant": round(cum, 2),
		})

	# TC order through to the first harvest off those cuttings.
	lead = v.supplier_lead_weeks or 0
	estab = v.ms_establishment_weeks or 0
	journey = [
		{"week": 0, "label": "Place TC order", "note": None},
		{"week": lead, "label": "TC arrives", "note": f"{lead}w supplier lead"},
		{"week": lead + (v.weeks_on_tray or 0), "label": "Tray to pot",
		 "note": f"{v.weeks_on_tray or 0}w tray"},
		{"week": lead + (v.weeks_on_tray or 0) + (v.weeks_on_pot or 0),
		 "label": "Pot ends, ramp begins", "note": f"{v.weeks_on_pot or 0}w pot"},
		{"week": lead + estab, "label": "Motherstock ready",
		 "note": f"{estab}w establishment, ramp {v.ramp_profile or ''}"},
		{"week": lead + estab + (v.hardening_weeks or 0), "label": "Cutting to field",
		 "note": f"{v.hardening_weeks or 0}w hardening"},
		{"week": lead + estab + (v.hardening_weeks or 0) + (v.weeks_to_pinch or 0),
		 "label": "Pinch", "note": f"{v.weeks_to_pinch or 0}w to pinch"},
		{"week": lead + estab + (v.cutting_to_harvest_weeks or 0),
		 "label": "First harvest",
		 "note": f"{v.cutting_to_harvest_weeks or 0}w cutting to harvest"},
	]

	ramp = [
		{"week": i + 1, "pct": int(round(x * 100))}
		for i, x in enumerate(
			__import__("upande_summer_flowers.summer_flowers.lifecycle_sim",
			           fromlist=["parse_ramp"]).parse_ramp(v.ramp_profile, v.ramp_weeks))
	]

	sibling = frappe.get_all(
		"Crop Protocol Version",
		filters={"crop_protocol": v.crop_protocol, "farm": v.farm},
		fields=["name", "version", "version_status", "effective_from", "effective_to",
		        "is_current", "change_reason"],
		order_by="version desc",
	)

	return {
		"version": v.name,
		"variety": v.variety,
		"farm": v.farm,
		"crop_protocol": v.crop_protocol,
		"status": v.version_status,
		"version_no": v.version,
		"effective_from": str(v.effective_from) if v.effective_from else None,
		"effective_to": str(v.effective_to) if v.effective_to else None,
		"is_current": bool(v.is_current),
		"change_reason": v.change_reason,
		"editable": v.version_status == "Draft",
		"climate_note": v.climate_note,
		"fields": {k: v.get(k) for k in EDITABLE_PROTOCOL_FIELDS},
		"derived": {
			"sqm_net_per_bed": flt(v.sqm_net_per_bed),
			"sqm_gross_per_bed": flt(v.sqm_gross_per_bed),
			"plants_per_sqm_gross": flt(v.plants_per_sqm_gross),
			"plants_per_ha": plants_per_ha,
			"plants_per_block": v.plants_per_block or 0,
			"min_planting_plants": v.min_planting_plants or 0,
			"total_flushes": v.total_flushes or 0,
			"total_stems_per_plant_life": flt(v.total_stems_per_plant_life),
			"stems_per_ha_life": flt(v.stems_per_ha_life),
			"stems_per_ha_year": flt(v.stems_per_ha_year),
			"stated_yield_stems_per_ha": flt(v.stated_yield_stems_per_ha),
			"yield_variance_pct": flt(v.yield_variance_pct),
			"total_weeks_in_ground": v.total_weeks_in_ground or 0,
			"life_expectancy_years": flt(v.life_expectancy_years),
			"first_harvest_offset_weeks": v.first_harvest_offset_weeks or 0,
			"harvest_weeks_per_year": v.harvest_weeks_per_year or 0,
			"flushes_per_year": flt(v.flushes_per_year),
			"establishment_weeks": v.establishment_weeks or 0,
			"ms_establishment_weeks": v.ms_establishment_weeks or 0,
			"cutting_to_harvest_weeks": v.cutting_to_harvest_weeks or 0,
			"total_renewal_lead_weeks": lead + estab,
			"plants_per_sqm_bench": flt(v.plants_per_sqm_bench),
			"max_multiplication_factor": flt(v.max_multiplication_factor),
			"lead_time_weeks": v.lead_time_weeks or 0,
			"cuttings_per_plant_required": flt(v.cuttings_per_plant_required),
			"grade_total_pct": flt(v.grade_total_pct),
			"order_to_first_harvest_weeks": journey[-1]["week"],
		},
		"flushes": flushes,
		"grades": [{"grade": g.grade, "pct": flt(g.allocation_pct),
		            "price": flt(g.price_per_stem)} for g in v.grade_allocation],
		"ramp": ramp,
		"journey": journey,
		"versions": sibling,
	}


@frappe.whitelist()
def save_protocol(version, changes, flushes=None, grades=None, change_reason=None,
                  effective_from=None):
	"""Apply protocol edits, honouring the versioning rules.

	A Draft is edited in place. An Active version is never edited: it is amended
	into a new Draft, because crop cycles are pinned to the parameters they ran
	under and rewriting those retrospectively would falsify their history.
	"""
	_guard()
	if isinstance(changes, str):
		changes = frappe.parse_json(changes or "{}")
	if isinstance(flushes, str):
		flushes = frappe.parse_json(flushes or "null")
	if isinstance(grades, str):
		grades = frappe.parse_json(grades or "null")

	v = frappe.get_doc("Crop Protocol Version", version)
	amended = False
	if v.version_status != "Draft":
		if not change_reason:
			frappe.throw(
				_("Version {0} is {1}. Editing it would rewrite the parameters existing "
				  "cycles ran under, so a new version is created instead — which needs a "
				  "change reason.").format(v.name, v.version_status),
				title=_("Change reason required"),
			)
		v = frappe.get_doc("Crop Protocol Version",
		                   v.create_amendment(change_reason, effective_from))
		amended = True

	for k, val in (changes or {}).items():
		if k in EDITABLE_PROTOCOL_FIELDS:
			v.set(k, val)

	if flushes is not None:
		v.flush_schedule = []
		for i, f in enumerate(flushes, start=1):
			v.append("flush_schedule", {
				"flush_number": i,
				"weeks_from_pinch": frappe.utils.cint(f.get("weeks_from_pinch")),
				"stems_per_plant": flt(f.get("stems_per_plant")),
			})
	if grades is not None:
		v.grade_allocation = []
		for g in grades:
			v.append("grade_allocation", {
				"grade": g.get("grade"),
				"allocation_pct": flt(g.get("pct")),
				"price_per_stem": flt(g.get("price")),
			})

	v.flags.ignore_permissions = True
	v.save()
	return {"version": v.name, "amended": amended, "status": v.version_status,
	        "detail": protocol_detail(v.name)}


@frappe.whitelist()
def beds_under_block(block=None, farm=None):
	"""Every bed under a block, with what is standing on it."""
	_guard()
	f = {}
	if block:
		f["custom_block"] = block
	elif farm:
		f["custom_block"] = ["in", frappe.get_all(
			"Block", filters={"farm": farm, "custom_is_summer_flower_block": 1}, pluck="name")]
	else:
		f["custom_block"] = ["is", "set"]

	rows = frappe.get_all(
		"Bed", filters=f,
		fields=["name", "bed", "greenhouse", "custom_block", "custom_bed_status",
		        "custom_planting_calendar", "custom_plants", "custom_uproot_date",
		        "bed_length", "bed_width"],
		order_by="custom_block asc, bed asc",
	)
	by_block = {}
	for r in rows:
		by_block.setdefault(r.custom_block, []).append({
			"bed": r.name, "number": r.bed, "status": r.custom_bed_status or "Empty",
			"planting": r.custom_planting_calendar, "plants": r.custom_plants or 0,
			"uproot_date": str(r.custom_uproot_date) if r.custom_uproot_date else None,
			"area": flt(r.bed_length) * flt(r.bed_width),
		})
	return {
		"blocks": [
			{
				"block": b,
				"block_code": (b or "").split(" - Block ")[-1],
				"beds": beds,
				"total": len(beds),
				"planted": len([x for x in beds if x["status"] in ("Planted", "Producing")]),
				"uprooted": len([x for x in beds if x["status"] == "Uprooted"]),
				"empty": len([x for x in beds if x["status"] == "Empty"]),
				"plants": sum(x["plants"] for x in beds),
			}
			for b, beds in sorted(by_block.items())
		],
		"total_beds": len(rows),
	}


@frappe.whitelist()
def uproot_bed_whatif(planting, bed, on_date, plan=None):
	"""Effect of uprooting one bed on production against demand. Nothing is saved."""
	_guard()
	doc = frappe.get_doc("Planting Calendar", planting)
	row = next((r for r in doc.bed_allocation if r.bed == bed), None)
	if not row:
		frappe.throw(_("Bed {0} is not allocated to {1}.").format(bed, planting))

	on_date = getdate(on_date)
	per_bed = row.plants or 0
	before, after, deltas = [], [], []
	for fl in doc.flush_projection:
		hd = getdate(fl.harvest_date)
		was = fl.expected_stems or 0
		# Only flushes on or after the uproot date lose this bed.
		lost = int(round((fl.stems_per_plant or 0) * per_bed)) if hd >= on_date else 0
		before.append({"flush": fl.flush_number, "date": str(hd),
		               "label": f"{fl.year}-W{fl.week_no:02d}", "stems": was})
		after.append({"flush": fl.flush_number, "date": str(hd),
		              "label": f"{fl.year}-W{fl.week_no:02d}", "stems": was - lost})
		deltas.append({"flush": fl.flush_number, "label": f"{fl.year}-W{fl.week_no:02d}",
		               "date": str(hd), "lost": lost, "affected": bool(lost)})

	# Put the loss against the plan's demand for those weeks.
	weeks = {}
	plan = resolve_plan(variety=doc.variety, plan=plan)
	if plan:
		p = frappe.get_doc("Summer Flower Production Plan", plan)
		weeks = {(w.year, w.week_no): w for w in p.plan_weeks}

	impact = []
	for fl, d in zip(doc.flush_projection, deltas):
		if not d["lost"]:
			continue
		w = weeks.get((fl.year, fl.week_no))
		dem = (w.demand_stems or 0) if w else 0
		prod = (w.production_stems or 0) if w else 0
		impact.append({
			"label": d["label"], "date": d["date"], "flush": fl.flush_number,
			"lost": d["lost"], "demand": dem,
			"production_before": prod, "production_after": prod - d["lost"],
			"variance_before": prod - dem, "variance_after": prod - d["lost"] - dem,
			"turns_short": (prod - dem) >= 0 and (prod - d["lost"] - dem) < 0,
		})

	return {
		"planting": planting, "bed": bed, "bed_number": row.bed_number,
		"on_date": str(on_date), "plants_on_bed": per_bed, "plan": plan,
		"stems_before": sum(b["stems"] for b in before),
		"stems_after": sum(a["stems"] for a in after),
		"stems_lost": sum(d["lost"] for d in deltas),
		"flushes_affected": len([d for d in deltas if d["affected"]]),
		"flushes": deltas, "impact": impact,
		"weeks_turned_short": len([i for i in impact if i["turns_short"]]),
	}


EVENT_TYPES = {
	"tc_order": "TC order",
	"tc_arrive": "TC arrives",
	"ms_first_cut": "Motherstock first cut",
	"ms_expiry": "Motherstock expires",
	"stick": "Stick cuttings",
	"plant": "Plant out",
	"pinch": "Pinch",
	"harvest": "Harvest",
	"uproot": "Uproot",
}


@frappe.whitelist()
def event_calendar(plan=None, variety=None, farm=None, year=None):
	"""Every dated event the demand and plan imply, for a calendar view."""
	_guard()
	events = []

	def add(date, kind, title, ref=None, block=None, qty=None):
		if not date:
			return
		d = getdate(date)
		if year and d.year != int(year):
			return
		y, w = iso_year_week(d)
		events.append({
			"date": str(d), "year": d.year, "month": d.month, "day": d.day,
			"iso_year": y, "week_no": w, "kind": kind,
			"kind_label": EVENT_TYPES.get(kind, kind),
			"title": title, "ref": ref, "block": block, "qty": qty,
		})

	pfilters = {"calendar_status": ["not in", ("Cancelled",)]}
	if variety:
		pfilters["variety"] = variety
	if farm:
		pfilters["farm"] = farm

	for pl in frappe.get_all(
		"Planting Calendar", filters=pfilters,
		fields=["name", "block", "variety", "beds", "plants", "sticking_date",
		        "planting_date", "pinch_date", "planned_uproot_date",
		        "actual_uproot_date"],
	):
		short = (pl.block or "").split(" - Block ")[-1]
		add(pl.sticking_date, "stick", f"Stick {pl.plants:,} cuttings", pl.name, short, pl.plants)
		add(pl.planting_date, "plant", f"Plant {pl.beds} beds ({pl.plants:,})", pl.name,
		    short, pl.plants)
		add(pl.pinch_date, "pinch", "Pinch", pl.name, short)
		add(pl.actual_uproot_date or pl.planned_uproot_date, "uproot", "Uproot", pl.name, short)
		for f in frappe.get_all(
			"Planting Calendar Flush", filters={"parent": pl.name},
			fields=["flush_number", "harvest_date", "expected_stems", "actual_stems",
			        "is_harvested"], order_by="flush_number asc",
		):
			stems = (f.actual_stems if f.is_harvested else f.expected_stems) or 0
			add(f.harvest_date, "harvest",
			    f"Flush {f.flush_number}: {stems:,} stems", pl.name, short, stems)

	for b in frappe.get_all(
		"Summer Flower Motherstock Batch",
		filters={k: v for k, v in (("variety", variety), ("farm", farm)) if v},
		fields=["name", "variety", "tc_plants_required", "tc_order_date",
		        "tc_on_farm_date", "max_pc_date", "expiry_date"],
	):
		add(b.tc_order_date, "tc_order",
		    f"Order {(b.tc_plants_required or 0):,} TC plantlets", b.name, None,
		    b.tc_plants_required)
		add(b.tc_on_farm_date, "tc_arrive", "TC plantlets on farm", b.name)
		add(b.max_pc_date, "ms_first_cut", "Motherstock productive", b.name)
		add(b.expiry_date, "ms_expiry", "Motherstock expires", b.name)

	events.sort(key=lambda e: (e["date"], e["kind"]))
	by_month = {}
	for e in events:
		by_month.setdefault(f"{e['year']}-{e['month']:02d}", []).append(e)
	counts = {}
	for e in events:
		counts[e["kind"]] = counts.get(e["kind"], 0) + 1

	return {
		"events": events,
		"by_month": by_month,
		"years": sorted({e["year"] for e in events}),
		"counts": counts,
		"kinds": EVENT_TYPES,
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


@frappe.whitelist()
def propagation_detail(plan=None, propagation_plan=None, variety=None, farm=None):
	"""The propagation plan behind a production plan: weeks, sources, TC schedule."""
	_guard()
	if not propagation_plan:
		plan = resolve_plan(variety, farm, plan)
		if not plan:
			return {"propagation_plan": None, "reason": "no production plan in scope"}
		rows = frappe.get_all("Summer Flower Propagation Plan",
		                      filters={"production_plan": plan}, pluck="name",
		                      order_by="creation desc", limit=1)
		if not rows:
			return {"propagation_plan": None, "plan": plan,
			        "reason": "not created yet"}
		propagation_plan = rows[0]

	d = frappe.get_doc("Summer Flower Propagation Plan", propagation_plan)
	batches = frappe.get_all(
		"Summer Flower Motherstock Batch",
		filters={"production_plan": d.production_plan},
		fields=["name", "batch_status", "mother_plants", "tc_plants_required",
		        "tc_order_date", "tc_on_farm_date", "first_sticking_date",
		        "expiry_date", "total_cost", "currency", "bench_sqm"])
	# The protocol is the production plan's, and so is the question of whether it
	# has moved since the numbers were built. Judged live: an approved plan never
	# runs validate again, so its stored verdict would be frozen at approval.
	pp = protocol_freshness(
		frappe.get_doc("Summer Flower Production Plan", d.production_plan))
	return {
		"propagation_plan": d.name,
		"plan": d.production_plan,
		"variety": d.variety, "farm": d.farm, "status": d.status,
		"currency": d.currency,
		"protocol": d.protocol,
		"protocol_status": pp["status"],
		"protocol_stale": cint(pp["stale"]),
		"protocol_note": pp["note"],
		"totals": {
			"plants_to_stick": cint(d.total_plants_to_stick),
			"cuttings_required": cint(d.total_cuttings_required),
			"cuttings_per_plant": flt(d.cuttings_per_plant),
			"weeks_sticking": cint(d.weeks_sticking),
			"peak_weekly_cuttings": cint(d.peak_weekly_cuttings),
			"peak_week": d.peak_week,
			"from_existing": cint(d.cuttings_from_existing),
			"from_new": cint(d.cuttings_from_new),
			"uncovered": cint(d.cuttings_uncovered),
			"existing_cover_pct": flt(d.existing_cover_pct),
			"mother_plants": cint(d.mother_plants_required),
			"bench_sqm": flt(d.peak_bench_sqm),
			"tc_plants": cint(d.tc_plants_required),
			"tc_order_date": str(d.tc_order_date or ""),
			"tc_on_farm_date": str(d.tc_on_farm_date or ""),
			"first_sticking_date": str(d.first_sticking_date or ""),
			"tc_cost": flt(d.tc_cost), "total_cost": flt(d.total_cost),
			"batches_created": cint(d.motherstock_batches_created),
			"requests_created": cint(d.seedling_requests_created),
			"ramp_weeks": cint(d.ramp_weeks),
			"full_capacity_date": str(d.full_capacity_date or ""),
			"lost_to_ramp": cint(d.cuttings_lost_to_ramp),
			"ramp_short_weeks": cint(d.ramp_short_weeks),
			"mother_plants_to_cover_ramp": cint(d.mother_plants_to_cover_ramp),
			"plants_short": cint(d.plants_short),
			"stems_at_risk": cint(d.stems_at_risk),
		},
		"warning": d.schedule_warning,
		"weeks": [{
			"year": r.year, "week_no": r.week_no,
			"label": "%s-W%02d" % (r.year, cint(r.week_no)),
			"week_start_date": str(r.week_start_date or ""),
			"plants_to_stick": cint(r.plants_to_stick),
			"cuttings_required": cint(r.cuttings_required),
			"from_existing_ms": cint(r.from_existing_ms),
			"from_new_ms": cint(r.from_new_ms),
			"shortfall": cint(r.shortfall),
			"capacity": cint(r.capacity_available),
			"ramp_pct": flt(r.ramp_pct),
			"plant_week": r.plant_week,
		} for r in d.weeks],
		"sources": [{
			"source_type": r.source_type, "batch": r.motherstock_batch,
			"mother_plants": cint(r.mother_plants),
			"weekly_capacity": cint(r.weekly_capacity),
			"available_from": str(r.available_from or ""),
			"available_to": str(r.available_to or ""),
			"notes": r.notes,
		} for r in d.sources],
		"batches": batches,
	}
