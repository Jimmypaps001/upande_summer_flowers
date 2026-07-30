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
def simulate_lifecycle(version, tc_qty, order_date, num_cycles=4, to_prop_pct=0,
                       farm_overrides=None, max_bench_sqm=None):
	"""The weekly TC -> motherstock -> cuttings lifecycle, with propagation feedback."""
	_guard()
	from upande_summer_flowers.summer_flowers import lifecycle_sim as ls

	if isinstance(farm_overrides, str):
		farm_overrides = frappe.parse_json(farm_overrides or "{}")
	if max_bench_sqm in (None, ""):
		max_bench_sqm = frappe.db.get_single_value("Summer Flower Settings", "max_bench_sqm")

	p = ls.params_from_version(version)
	res = ls.simulate(
		p, frappe.utils.cint(tc_qty), order_date,
		num_cycles=frappe.utils.cint(num_cycles),
		farm_overrides=farm_overrides,
		default_to_prop_pct=flt(to_prop_pct),
		max_bench_sqm=max_bench_sqm,
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

	out = []
	for b in all_blocks:
		rows = by_block.get(b.name, [])
		entry = {
			"block": b.name,
			"block_code": b.block,
			"farm": b.farm,
			"total_beds": b.custom_total_beds or 0,
			"gross_area_ha": flt(b.custom_gross_area_ha),
			"plantings": [],
			"status": "Not planted",
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

	return {
		"blocks": out,
		"totals": {
			"blocks": len(out),
			"planted": len([b for b in out if b["plantings"]]),
			"idle": len([b for b in out if not b["plantings"]]),
			"stems": sum(p["expected_stems_life"] for b in out for p in b["plantings"]),
		},
	}


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
	if plan is None:
		rows = frappe.get_all("Summer Flower Production Plan",
		                      filters={"variety": doc.variety, "docstatus": ["<", 2]},
		                      pluck="name", order_by="creation desc", limit=1)
		plan = rows[0] if rows else None
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
