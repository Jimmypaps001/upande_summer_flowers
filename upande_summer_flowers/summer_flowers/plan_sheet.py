# Copyright (c) 2026, James Kiruga and contributors
"""The production plan in the shape it is actually read in."""

import datetime
import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def build(plan):
	"""Arrange the plan as the workbook's rows and columns."""
	if isinstance(plan, str):
		plan = frappe.get_doc("Summer Flower Production Plan", plan)
	protocol = frappe.get_cached_doc("Crop Protocol Version", plan.protocol) \
		if plan.protocol else None

	# A crop that is never pinched has no pinching date, and a column of planting
	# dates relabelled "pinching" is how a sheet starts lying about the protocol.
	pinched = bool(cint(protocol.weeks_to_pinch)) if protocol else False

	data = json.loads(plan.matrix_json) if plan.matrix_json else {"grid": [], "rows": []}
	grid = [(cint(y), cint(w), m) for y, w, m in data.get("grid", [])]
	if not grid:
		grid = [(cint(w.year), cint(w.week_no), str(w.week_start_date))
		        for w in plan.plan_weeks]
	stems = data.get("rows", [])

	plantings = []
	for i, row in enumerate(plan.plan_blocks):
		mine = stems[i]["weeks"] if i < len(stems) else {}
		extra = stems[i] if i < len(stems) else {}
		weekly = [cint(mine.get("%s-%s" % (y, w), 0)) for (y, w, _m) in grid]
		plantings.append({
			"block": row.block or _("(no block)"),
			"planting_week": row.planting_week,
			"planting_date": str(row.planting_date or ""),
			"pinch_date": str(row.pinch_date or "") if pinched else "",
			# The route's own milestones, so a row reads the way the protocol runs:
			# material in, into the ground, first cut.
			"sticking_week": (
				"%s-W%02d" % (cint(row.sticking_year), cint(row.sticking_week))
				if cint(row.sticking_year) and cint(row.sticking_week) else ""),
			"first_cut": (
				"%s-W%02d" % (cint(row.first_harvest_year), cint(row.first_harvest_week))
				if cint(row.first_harvest_year) and cint(row.first_harvest_week) else ""),
			# Rounded: net_area_ha carries the division by 10,000 that made it, and
			"net_area_sqm": round(flt(row.net_area_ha) * 10_000, 2),
			"uproot_date": extra.get("uproot", ""),
			"beds": cint(row.beds),
			"plants": cint(row.plants),
			"weekly": weekly,
			"season_total": sum(weekly),
			"lifetime_total": cint(row.lifetime_stems),
			# The workbook's last column is not a lifetime total, which is what it
			"season_stems_per_ha": (
				sum(weekly) / (flt(row.net_area_ha) or 1)
				if flt(row.net_area_ha) else 0),
			"is_new": bool(row.is_new_planting),
			"not_placed": bool(row.not_placed),
		})

	weekly_prod = [sum(p["weekly"][i] for p in plantings) for i in range(len(grid))]
	demand = {(cint(w.year), cint(w.week_no)): cint(w.demand_stems) for w in plan.plan_weeks}
	weekly_dem = [demand.get((y, w), 0) for (y, w, _m) in grid]
	area = {(cint(w.year), cint(w.week_no)): flt(w.area_ha) for w in plan.plan_weeks}

	# Monthly figures are shown against the first week of each month, exactly as the
	monthly_prod, monthly_dem = [0] * len(grid), [0] * len(grid)
	first_of_month = {}
	for i, (y, w, m) in enumerate(grid):
		key = (getdate(m).year, getdate(m).month)
		first_of_month.setdefault(key, i)
	for i, (y, w, m) in enumerate(grid):
		key = (getdate(m).year, getdate(m).month)
		monthly_prod[first_of_month[key]] += weekly_prod[i]
		monthly_dem[first_of_month[key]] += weekly_dem[i]

	# The split by stem length comes off the protocol's grade allocation, so changing
	grades = []
	total_pct = 0
	# Every grade is valued at the variety Item's valuation; nothing is typed per grade.
	from upande_summer_flowers.summer_flowers.valuation import stem_price

	price = stem_price(plan.variety, plan.get("company"))
	for g in (protocol.grade_allocation if protocol else []):
		pct = flt(g.allocation_pct)
		total_pct += pct
		grades.append({
			"grade": g.grade,
			"pct": pct,
			"weekly": [int(round(v * pct / 100)) for v in weekly_prod] if pct else [],
			"demand": [int(round(v * pct / 100)) for v in weekly_dem] if pct else [],
			"price_per_stem": price,
		})

	route = route_of(protocol)
	return {
		"plan": plan.name,
		# Planting to meet demand is the whole exercise, so the sheet says plainly
		# which weeks it misses and what planting them would have taken.
		"gaps": shortfalls(grid, weekly_prod, weekly_dem, plantings, protocol, route),
		"variety": plan.variety,
		"farm": plan.farm,
		"season": plan.season,
		"protocol": plan.protocol,
		"grid": [{"year": y, "week": w, "monday": m} for (y, w, m) in grid],
		"plantings": plantings,
		"totals": {
			"season_stems_per_ha": (
				sum(p["season_total"] for p in plantings)
				/ sum(flt(p["net_area_sqm"]) / 10_000 for p in plantings)
				if sum(flt(p["net_area_sqm"]) for p in plantings) else 0),
			"net_area_sqm": round(sum(p["net_area_sqm"] for p in plantings), 2),
			"beds": sum(p["beds"] for p in plantings),
			"plants": sum(p["plants"] for p in plantings),
			"season": sum(p["season_total"] for p in plantings),
			"lifetime": sum(p["lifetime_total"] for p in plantings),
		},
		"weekly_production": weekly_prod,
		"monthly_production": monthly_prod,
		"weekly_demand": weekly_dem,
		"monthly_demand": monthly_dem,
		"difference": [p - d for p, d in zip(weekly_prod, weekly_dem)],
		"area": [area.get((y, w), 0) for (y, w, _m) in grid],
		"pinched": pinched,
		"route": route,
		"grades": grades,
		"grade_total_pct": total_pct,
		"assumptions": assumptions(protocol),
	}


def shortfalls(grid, prod, dem, plantings, protocol, route):
	"""Each week the plan misses, and the planting that would have covered it."""
	if not protocol:
		return []
	# The best weekly cut per plant this plan actually achieves. Anything less
	# optimistic would understate the plants, and the number is meant to be the
	# floor: "no fewer than this many".
	rate = 0.0
	for p in plantings:
		if p["plants"] and p["weekly"]:
			rate = max(rate, max(p["weekly"]) / float(p["plants"]))
	plant_to_cut = cint(protocol.first_harvest_offset_weeks)
	# Order to in-the-ground, read off the same journey the sheet draws.
	order_to_plant = max(
		[cint(j["week"]) for j in (route or {}).get("journey") or []
		 if str(j.get("label", "")).startswith(_("Planted"))] or [0])

	out = []
	for i, (y, w, monday) in enumerate(grid):
		short = cint(dem[i]) - cint(prod[i])
		if not cint(dem[i]) or short <= 0:
			continue
		plant_on = getdate(monday) - datetime.timedelta(weeks=plant_to_cut)
		out.append({
			"week": "%s-W%02d" % (y, w),
			"short": int(short),
			"short_pct": round(short * 100.0 / cint(dem[i]), 1),
			"plants": int(math.ceil(short / rate)) if rate else None,
			"plant_in": _iso_week(plant_on),
			"order_by": _iso_week(plant_on - datetime.timedelta(weeks=order_to_plant)),
			"passed": plant_on < getdate(),
		})
	return out


def _iso_week(d):
	y, w, _dow = d.isocalendar()
	return "%s-W%02d" % (y, w)


def route_of(v):
	"""The way this crop reaches the ground, as the protocol lays it out."""
	if not v:
		return None
	from upande_summer_flowers.summer_flowers import planning_api, route_ways

	ways = [w.route for w in (v.get(route_ways.WAYS) or []) if w.get("route")]
	try:
		journey, title = planning_api._route_journey(
			v, cint(v.first_harvest_offset_weeks))
	except Exception:
		frappe.clear_last_message()
		journey, title = [], None
	return {"ways": ways, "journey": journey, "title": title,
	        "lifts": [{"lift": i + 1, "weeks_after_arrival": cint(a),
	                   "share_pct": round(share * 100, 1)}
	                  for i, (a, share) in enumerate(_entry_waves(v))]}


def _entry_waves(v):
	"""How a consignment is handed over, where the protocol states a profile."""
	from upande_summer_flowers.summer_flowers import route_ways

	for w in (v.get(route_ways.WAYS) or []):
		stages = route_ways.ROUTES.get(w.route) or []
		waves = planning_lifts(v, stages[0] if stages else None)
		if len(waves) > 1:
			return waves
	return []


def planning_lifts(v, stage):
	from upande_summer_flowers.summer_flowers import planning_api

	return planning_api._entry_lifts(v, stage) if stage else []


def assumptions(v):
	"""The protocol, laid out as the workbook's assumptions block."""
	if not v:
		return {}
	return {
		"net_sqm_per_bed": flt(v.sqm_net_per_bed),
		"plants_per_sqm_net": flt(v.plants_per_sqm_net),
		"plants_per_bed": cint(v.plants_per_bed),
		"plants_per_net_ha": cint(v.plants_per_net_ha),
		"flushes_per_year": flt(v.flushes_per_year),
		"flush_schedule": [
			{"flush": cint(r.flush_number),
			 "weeks_from_pinch": cint(r.weeks_from_pinch),
			 "weeks_from_planting": cint(r.weeks_from_planting),
			 "stems_per_plant": flt(r.stems_per_plant),
			 "stems_per_ha_year": flt(r.stems_per_plant) * cint(v.plants_per_net_ha)}
			for r in v.flush_schedule
		],
		"total_stems_per_plant_life": flt(v.total_stems_per_plant_life),
		"stems_per_net_ha_life": flt(v.stems_per_ha_life),
		"pinched": bool(cint(v.weeks_to_pinch)),
		"pinch_at_week": cint(v.weeks_to_pinch),
		"weeks_planting_to_first_harvest": cint(v.first_harvest_offset_weeks),
		"total_weeks_in_ground": cint(v.total_weeks_in_ground),
		"stems_per_net_ha_year": flt(v.stems_per_ha_year),
		"best_year_stems_per_plant": flt(v.best_year_stems_per_plant),
		"best_year_stems_per_net_ha": flt(v.best_year_stems_per_net_ha),
	}


@frappe.whitelist()
def sheet(plan):
	frappe.has_permission("Summer Flower Production Plan", "read", plan, throw=True)
	return build(plan)


@frappe.whitelist()
def sheet_csv(plan):
	"""The same sheet as CSV, so it opens in Excel next to the original."""
	frappe.has_permission("Summer Flower Production Plan", "read", plan, throw=True)
	d = build(plan)
	head = (["BLOCK", "Planting wk", "Planting Date"]
	        + (["Pinching Date"] if d["pinched"] else [])
	        + ["First Cut", "NET AREA (m2)", "Uprooting Date", "NET AREA (Ha)",
	           "NO OF BEDS", "NO.PLANTS"])
	weeks = [str(g["week"]) for g in d["grid"]]
	out = [["Year"] + [""] * (len(head) - 1) + [str(g["year"]) for g in d["grid"]],
	       ["Week starting"] + [""] * (len(head) - 1) + [g["monday"] for g in d["grid"]],
	       head + weeks + ["TOTAL", "STEMS/HA", "LIFETIME (STEMS)"]]

	for p in d["plantings"]:
		out.append([
			p["block"], p["planting_week"], p["planting_date"]]
			+ ([p["pinch_date"]] if d["pinched"] else [])
			+ [p["first_cut"], p["net_area_sqm"], p["uproot_date"],
			round(flt(p["net_area_sqm"]) / 10_000, 4), p["beds"], p["plants"],
		] + p["weekly"] + [p["season_total"],
		                   round(p["season_stems_per_ha"]),
		                   p["lifetime_total"]])

	t = d["totals"]
	pad = lambda label: [label] + [""] * (len(head) - 1)
	out.append(["TOTAL", "", ""] + ([""] if d["pinched"] else []) + ["", t["net_area_sqm"], "",
	            round(flt(t["net_area_sqm"]) / 10_000, 4), t["beds"], t["plants"]]
	           + [""] * len(weeks)
	           + [t["season"], round(t["season_stems_per_ha"]), t["lifetime"]])
	out.append([])
	out.append(pad("Weekly production") + d["weekly_production"])
	out.append(pad("Monthly production") + [v or "" for v in d["monthly_production"]])
	out.append([])
	for g in d["grades"]:
		out.append(pad("%s (%s%%)" % (g["grade"], g["pct"])) + (g["weekly"] or []))
	out.append([])
	for g in d["grades"]:
		out.append(pad("%s demand" % g["grade"]) + (g["demand"] or []))
	out.append(pad("Weekly market demand") + d["weekly_demand"])
	out.append(pad("Monthly market demand") + [v or "" for v in d["monthly_demand"]])
	out.append(pad("DIFFERENCE") + d["difference"])
	out.append(pad("Area (ha standing)") + [round(v, 4) for v in d["area"]])

	a = d["assumptions"]
	if a:
		out.append([])
		out.append(["%s assumptions -- from Crop Protocol %s" % (d["variety"], d["protocol"])])
		out.append(["", "Net m2 per bed", a["net_sqm_per_bed"]])
		out.append(["", "Plants/m2 (net)", a["plants_per_sqm_net"],
		            "Flushes / yr", round(a["flushes_per_year"], 4)])
		out.append(["", "Flush No", "Weeks from Pinch", "Stems/plant",
		            "Stems / ha / yr"])
		for f in a["flush_schedule"]:
			out.append(["", f["flush"], f["weeks_from_pinch"], f["stems_per_plant"],
			            round(f["stems_per_ha_year"])])
		out.append(["", "Total Stems Per plant life", a["total_stems_per_plant_life"]])
		out.append(["", "Stems/ha/life", round(a["stems_per_net_ha_life"])])
		out.append(["", "Pinch at week",
		            a["pinch_at_week"] if a["pinched"] else "not pinched"])
		out.append(["", "Planting to first harvest (weeks)",
		            a["weeks_planting_to_first_harvest"]])
		r = d.get("route") or {}
		if r.get("ways"):
			out.append(["", "Route", " / ".join(r["ways"])])
		for j in (r.get("journey") or []):
			out.append(["", "  wk %s" % j["week"], j["label"], j.get("note") or ""])
		out.append(["", "Total Weeks in Ground", a["total_weeks_in_ground"]])
		out.append(["", "Stems/ha/Yr", round(a["stems_per_net_ha_year"])])
		out.append(["", "Best Year: stems per plant", a["best_year_stems_per_plant"]])
		out.append(["", "Best Year: stems/ha", round(a["best_year_stems_per_net_ha"])])

	import csv
	import io

	buf = io.StringIO()
	csv.writer(buf).writerows(out)
	frappe.response["filename"] = "%s-planning-sheet.csv" % d["plan"]
	frappe.response["filecontent"] = buf.getvalue()
	frappe.response["type"] = "download"
	frappe.response["doctype"] = None
