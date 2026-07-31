# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Read-only endpoints backing the Summer Flowers dashboard."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from upande_summer_flowers.summer_flowers.planning import current_week, iso_monday


def _guard():
	"""Dashboard is desk-authenticated; never expose it to Guest."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to view the Summer Flowers dashboard."),
		             frappe.PermissionError)


@frappe.whitelist()
def filters():
	"""Farms and varieties that actually have Summer Flowers data."""
	_guard()
	protocols = frappe.get_all(
		"Summer Flower Protocol",
		filters={"protocol_status": "Active"},
		fields=["name", "variety", "farm"],
		order_by="farm asc, variety asc",
	)
	return {
		"farms": sorted({p.farm for p in protocols if p.farm}),
		"varieties": sorted({p.variety for p in protocols if p.variety}),
		"protocols": protocols,
	}


@frappe.whitelist()
def overview(farm=None, variety=None):
	"""Headline state: horizon, latest plan, coverage, motherstock."""
	_guard()
	dfilters = {}
	if farm:
		dfilters["farm"] = farm
	if variety:
		dfilters["variety"] = variety

	demands = frappe.get_all(
		"Summer Flower Market Demand",
		filters=dfilters,
		fields=["name", "variety", "farm", "horizon_start", "horizon_end",
		        "weeks_covered", "weeks_ahead_of_today", "horizon_gap_weeks",
		        "horizon_status", "total_demand_stems", "peak_weekly_demand",
		        "target_years_ahead"],
		order_by="modified desc",
	)

	plans = frappe.get_all(
		"Summer Flower Production Plan",
		filters=dfilters,
		fields=["name", "variety", "farm", "status", "workflow_state",
		        "market_demand",
		        "from_year", "from_week", "to_year", "to_week",
		        "total_production_stems", "total_demand_stems", "coverage_pct",
		        "weeks_in_deficit", "worst_weekly_deficit", "peak_weekly_sticking",
		        "peak_sticking_week", "new_beds_required", "budget", "docstatus"],
		order_by="creation desc",
		limit=20,
	)

	batches = frappe.get_all(
		"Summer Flower Motherstock Batch",
		filters=dfilters,
		fields=["name", "variety", "farm", "batch_status", "mother_plants",
		        "pots_required", "bench_sqm", "peak_bench_sqm", "tc_plants_required",
		        "build_up_cycles", "tc_order_date", "max_pc_date", "expiry_date",
		        "renewal_tc_order_date", "total_cost", "schedule_warning"],
		order_by="max_pc_date asc",
	)

	today = getdate(nowdate())
	for b in batches:
		b["renewal_overdue"] = bool(
			b.get("renewal_tc_order_date") and getdate(b["renewal_tc_order_date"]) < today
		)
		b["days_to_renewal"] = (
			(getdate(b["renewal_tc_order_date"]) - today).days
			if b.get("renewal_tc_order_date") else None
		)

	y, w = current_week()
	return {
		"today": {"date": str(today), "year": y, "week": w,
		          "week_start": str(iso_monday(y, w))},
		"demands": demands,
		"plans": plans,
		"motherstock": batches,
	}


@frappe.whitelist()
def monthly_series(plan=None, farm=None, variety=None):
	"""Monthly production vs demand for the chart."""
	_guard()
	if not plan:
		f = {"docstatus": ["<", 2]}
		if farm:
			f["farm"] = farm
		if variety:
			f["variety"] = variety
		rows = frappe.get_all(
			"Summer Flower Production Plan", filters=f, pluck="name",
			order_by="creation desc", limit=1,
		)
		if not rows:
			return {"plan": None, "months": []}
		plan = rows[0]

	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	return {
		"plan": doc.name,
		"variety": doc.variety,
		"farm": doc.farm,
		"status": doc.status,
		"months": [
			{
				"label": f"{m.month_name[:3]} {str(m.year)[2:]}",
				"year": m.year,
				"month": m.month,
				"production": m.production_stems or 0,
				"demand": m.demand_stems or 0,
				"variance": m.variance_stems or 0,
			}
			for m in doc.plan_months
		],
	}


@frappe.whitelist()
def block_coverage(farm=None):
	"""Block register with occupancy, plus what is standing on each."""
	_guard()
	f = {"block_status": "Active"}
	if farm:
		f["farm"] = farm
	blocks = frappe.get_all(
		"Summer Flower Block",
		filters=f,
		fields=["name", "block_code", "farm", "gross_area_ha", "total_beds",
		        "beds_occupied", "beds_free", "coverage_pct", "plants_standing",
		        "current_varieties"],
		order_by="farm asc, block_code asc",
	)
	totals = {
		"blocks": len(blocks),
		"beds": sum(b.total_beds or 0 for b in blocks),
		"occupied": sum(b.beds_occupied or 0 for b in blocks),
		"area_ha": sum(flt(b.gross_area_ha) for b in blocks),
		"plants": sum(b.plants_standing or 0 for b in blocks),
	}
	totals["coverage_pct"] = (
		totals["occupied"] / totals["beds"] * 100 if totals["beds"] else 0
	)
	return {"blocks": blocks, "totals": totals}


@frappe.whitelist()
def on_ground(farm=None, variety=None):
	"""Plantings currently standing, with their harvest weeks."""
	_guard()
	f = {"planting_status": ["!=", "Uprooted"]}
	if farm:
		f["farm"] = farm
	if variety:
		f["variety"] = variety
	rows = frappe.get_all(
		"Summer Flower Planting",
		filters=f,
		fields=["name", "block", "farm", "variety", "beds", "plants",
		        "gross_area_ha", "planting_date", "planting_year", "planting_week",
		        "planned_uproot_date", "actual_uproot_date", "planting_status",
		        "harvest_week_family", "first_harvest_year", "first_harvest_week",
		        "lifetime_stems"],
		order_by="planting_date desc",
	)
	today = getdate(nowdate())
	for r in rows:
		end = getdate(r.actual_uproot_date or r.planned_uproot_date)
		r["weeks_remaining"] = max(0, (end - today).days // 7)
	return rows


@frappe.whitelist()
def protocol_comparison(variety=None):
	"""Same variety across farms, so climate differences are visible side by side."""
	_guard()
	f = {"protocol_status": "Active"}
	if variety:
		f["variety"] = variety
	return frappe.get_all(
		"Summer Flower Protocol",
		filters=f,
		fields=["name", "variety", "farm", "plants_per_sqm_net", "weeks_to_pinch",
		        "flush_interval_weeks", "total_flushes", "total_weeks_in_ground",
		        "total_stems_per_plant_life", "stems_per_ha_year",
		        "harvest_weeks_per_year", "first_harvest_offset_weeks",
		        "establishment_weeks", "lead_time_weeks", "climate_note"],
		order_by="variety asc, farm asc",
	)
