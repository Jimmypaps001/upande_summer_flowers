# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The production plan in the shape it is actually read in.

"Aster Pink Flush planning.xlsx" lays a plan out as one row per planting and one
column per week: block, planting week, dates, area, beds, plants, then the stems
that planting yields in each week of the season, its season total and its
lifetime total. Under the plantings come weekly and monthly production, the split
by stem length, demand on the same two bases, the difference, and the area
standing. At the bottom sit the assumptions -- which is the crop protocol.

The plan held all of it except the middle: which planting puts which stems in
which week. _populate now records that as it folds the numbers in, so this module
only arranges what the plan already computed. Nothing here recalculates a stem.
"""

import json

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
			"pinch_date": str(row.pinch_date or ""),
			# Rounded: net_area_ha carries the division by 10,000 that made it, and
			# 28 beds of 50 m2 printed as 1400.0000000000002.
			"net_area_sqm": round(flt(row.net_area_ha) * 10_000, 2),
			"uproot_date": extra.get("uproot", ""),
			"gross_area_ha": flt(extra.get("gross_area_ha") or 0),
			"beds": cint(row.beds),
			"plants": cint(row.plants),
			"weekly": weekly,
			"season_total": sum(weekly),
			"lifetime_total": cint(row.lifetime_stems),
			# The workbook's last column is not a lifetime total, which is what it
			# looks like: 244,000 stems against 0.125 gross ha is the 1,952,000 it
			# prints. It is this season's yield intensity, per gross hectare, which is
			# how the farm compares one planting with another.
			"season_stems_per_gross_ha": (
				sum(weekly) / flt(extra.get("gross_area_ha"))
				if flt(extra.get("gross_area_ha")) else 0),
			"is_new": bool(row.is_new_planting),
			"not_placed": bool(row.not_placed),
		})

	weekly_prod = [sum(p["weekly"][i] for p in plantings) for i in range(len(grid))]
	demand = {(cint(w.year), cint(w.week_no)): cint(w.demand_stems) for w in plan.plan_weeks}
	weekly_dem = [demand.get((y, w), 0) for (y, w, _m) in grid]
	area = {(cint(w.year), cint(w.week_no)): flt(w.area_ha) for w in plan.plan_weeks}

	# Monthly figures are shown against the first week of each month, exactly as the
	# workbook does -- a monthly total spread across its weeks would double-count when
	# read down a column.
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
	# it there changes the sheet. Where the percentages are missing the rows are shown
	# empty rather than silently splitting evenly.
	grades = []
	total_pct = 0
	for g in (protocol.grade_allocation if protocol else []):
		pct = flt(g.allocation_pct)
		total_pct += pct
		grades.append({
			"grade": g.grade,
			"pct": pct,
			"weekly": [int(round(v * pct / 100)) for v in weekly_prod] if pct else [],
			"demand": [int(round(v * pct / 100)) for v in weekly_dem] if pct else [],
			"price_per_stem": flt(g.price_per_stem),
		})

	return {
		"plan": plan.name,
		"variety": plan.variety,
		"farm": plan.farm,
		"season": plan.season,
		"protocol": plan.protocol,
		"grid": [{"year": y, "week": w, "monday": m} for (y, w, m) in grid],
		"plantings": plantings,
		"totals": {
			"season_stems_per_gross_ha": (
				sum(p["season_total"] for p in plantings)
				/ sum(p["gross_area_ha"] for p in plantings)
				if sum(p["gross_area_ha"] for p in plantings) else 0),
			"net_area_sqm": round(sum(p["net_area_sqm"] for p in plantings), 2),
			"gross_area_ha": sum(p["gross_area_ha"] for p in plantings),
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
		"grades": grades,
		"grade_total_pct": total_pct,
		"assumptions": assumptions(protocol),
	}


def assumptions(v):
	"""The protocol, laid out as the workbook's assumptions block.

	Every line here is a field on Crop Protocol, so the way to change the sheet is
	to change the protocol and have it approved -- which is the point of the
	protocol being the only place these live.
	"""
	if not v:
		return {}
	return {
		"net_sqm_per_bed": flt(v.sqm_net_per_bed),
		"gross_sqm_per_bed": flt(v.sqm_gross_per_bed),
		"path_allowance_pct": flt(v.path_allowance_pct),
		"plants_per_sqm_net": flt(v.plants_per_sqm_net),
		"plants_per_bed": cint(v.plants_per_bed),
		"plants_per_net_ha": cint(v.plants_per_net_ha),
		"plants_per_gross_ha": cint(v.plants_per_gross_ha),
		"flushes_per_year": flt(v.flushes_per_year),
		"flush_schedule": [
			{"flush": cint(r.flush_number),
			 "weeks_from_pinch": cint(r.weeks_from_pinch),
			 "weeks_from_planting": cint(r.weeks_from_planting),
			 "stems_per_plant": flt(r.stems_per_plant),
			 "stems_per_net_ha_year": flt(r.stems_per_plant) * cint(v.plants_per_net_ha),
			 "stems_per_gross_ha_year": flt(r.stems_per_plant) * cint(v.plants_per_gross_ha)}
			for r in v.flush_schedule
		],
		"total_stems_per_plant_life": flt(v.total_stems_per_plant_life),
		"stems_per_net_ha_life": flt(v.stems_per_ha_life),
		"stems_per_gross_ha_life": flt(v.stems_per_gross_ha_life),
		"pinch_at_week": cint(v.weeks_to_pinch),
		"total_weeks_in_ground": cint(v.total_weeks_in_ground),
		"stems_per_net_ha_year": flt(v.stems_per_ha_year),
		"stems_per_gross_ha_year": flt(v.stems_per_gross_ha_year),
		"best_year_stems_per_plant": flt(v.best_year_stems_per_plant),
		"best_year_stems_per_net_ha": flt(v.best_year_stems_per_net_ha),
		"best_year_stems_per_gross_ha": flt(v.best_year_stems_per_gross_ha),
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
	head = ["BLOCK", "Planting wk", "Planting Date", "Pinching Date", "NET AREA (m2)",
	        "Uprooting Date", "GROSS AREA (Ha)", "NO OF BEDS", "NO.PLANTS"]
	weeks = [str(g["week"]) for g in d["grid"]]
	out = [["Year"] + [""] * (len(head) - 1) + [str(g["year"]) for g in d["grid"]],
	       ["Week starting"] + [""] * (len(head) - 1) + [g["monday"] for g in d["grid"]],
	       head + weeks + ["TOTAL", "STEMS/GROSS HA", "LIFETIME (STEMS)"]]

	for p in d["plantings"]:
		out.append([
			p["block"], p["planting_week"], p["planting_date"], p["pinch_date"],
			p["net_area_sqm"], p["uproot_date"], round(p["gross_area_ha"], 4),
			p["beds"], p["plants"],
		] + p["weekly"] + [p["season_total"],
		                   round(p["season_stems_per_gross_ha"]),
		                   p["lifetime_total"]])

	t = d["totals"]
	pad = lambda label: [label] + [""] * (len(head) - 1)
	out.append(["TOTAL", "", "", "", t["net_area_sqm"], "", round(t["gross_area_ha"], 4),
	            t["beds"], t["plants"]] + [""] * len(weeks)
	           + [t["season"], round(t["season_stems_per_gross_ha"]), t["lifetime"]])
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
		out.append(["", "Gross m2 per bed", a["gross_sqm_per_bed"],
		            "path allowance %s%%" % a["path_allowance_pct"]])
		out.append(["", "Plants/m2 (net)", a["plants_per_sqm_net"],
		            "Flushes / yr", round(a["flushes_per_year"], 4)])
		out.append(["", "Flush No", "Weeks from Pinch", "Stems/plant",
		            "Stems / net ha / yr", "Stems / gross ha / yr"])
		for f in a["flush_schedule"]:
			out.append(["", f["flush"], f["weeks_from_pinch"], f["stems_per_plant"],
			            round(f["stems_per_net_ha_year"]), round(f["stems_per_gross_ha_year"])])
		out.append(["", "Total Stems Per plant life", a["total_stems_per_plant_life"]])
		out.append(["", "Stems/net ha/life", round(a["stems_per_net_ha_life"])])
		out.append(["", "Stems/gross ha/life", round(a["stems_per_gross_ha_life"])])
		out.append(["", "Pinch at week", a["pinch_at_week"]])
		out.append(["", "Total Weeks in Ground", a["total_weeks_in_ground"]])
		out.append(["", "Stems/net ha/Yr", round(a["stems_per_net_ha_year"])])
		out.append(["", "Stems/gross ha/Yr", round(a["stems_per_gross_ha_year"])])
		out.append(["", "Best Year: stems per plant", a["best_year_stems_per_plant"]])
		out.append(["", "Best Year: stems/net ha", round(a["best_year_stems_per_net_ha"])])
		out.append(["", "Best Year: stems/gross ha", round(a["best_year_stems_per_gross_ha"])])

	import csv
	import io

	buf = io.StringIO()
	csv.writer(buf).writerows(out)
	frappe.response["filename"] = "%s-planning-sheet.csv" % d["plan"]
	frappe.response["filecontent"] = buf.getvalue()
	frappe.response["type"] = "download"
	frappe.response["doctype"] = None
