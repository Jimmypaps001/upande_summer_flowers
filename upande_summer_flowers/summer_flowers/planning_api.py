# Copyright (c) 2026, James Kiruga and contributors
"""Endpoints for the planning dashboard."""

import datetime
import math

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers.doctype.crop_protocol_version.crop_protocol_version import (
	bed_sqm_per_ha,
)
from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
	protocol_freshness,
)
from upande_summer_flowers.summer_flowers.motherstock_sim import (
	params_from_version,
	rounds_that_fit,
	simulate,
	tc_needed_for,
)
from upande_summer_flowers.summer_flowers.planning import (
	season_year,
	season_first_week,
	iso_monday,
	iso_year_week,
	week_sequence,
)


def _guard():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)


def resolve_plan(variety=None, farm=None, plan=None):
	"""The plan the dashboard should show when none was asked for."""
	if plan:
		return plan
	f = {"docstatus": ["<", 2]}
	# Kept out of the return so every existing caller is untouched: what this
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
		        "market_demand", "variety", "farm", "season", "season_start_year",
		        "from_year", "from_week",
		        "to_year", "to_week", "weeks_covered", "total_demand_stems",
		        "total_production_stems", "coverage_pct", "weeks_in_deficit",
		        "new_beds_required", "creation"],
		order_by="creation desc",
	)
	# Every plan carried the same period label, so eighteen of them read as
	from upande_summer_flowers.summer_flowers.crop_protocol import variety_names
	named = variety_names([r.variety for r in rows])
	for r in rows:
		r["label"] = "%s · %s · %s · %s%s · %.0f%% of demand" % (
			r.name, r.season or _("no season"), named.get(r.variety) or r.variety or "?",
			r.workflow_state or r.status or "?",
			" + budget" if r.budget else "", flt(r.coverage_pct))
		r["is_authoritative"] = r.docstatus == 1
	drafts = [r for r in rows if r.docstatus == 0]

	# The financial years the demand register covers, which are not the same as the
	regf = {}
	if variety:
		regf["variety"] = variety
	if farm:
		regf["farm"] = farm
	reg = frappe.get_all("Summer Flower Market Demand", filters=regf, pluck="name")
	seasons = set()
	if reg:
		for w in frappe.get_all(
			"Summer Flower Demand Week",
			filters={"parent": ["in", reg],
			         "parenttype": "Summer Flower Market Demand"},
			fields=["year", "week_no"],
		):
			y, wk = cint(w.year), cint(w.week_no)
			if y and wk:
				# July to June: W27 on belongs to the year it starts in.
				seasons.add(y if wk >= season_first_week() else y - 1)

	return {
		"plans": rows,
		"default": resolve_plan(variety, farm),
		"demand_seasons": sorted(seasons),
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
	from upande_summer_flowers.summer_flowers.crop_protocol import variety_names
	varieties = sorted({r.variety for r in rows if r.variety})
	return {
		"versions": rows,
		"varieties": varieties,
		# What the grower calls each one. The page prints these as plain text,
		"variety_names": variety_names(varieties),
		"farms": sorted({r.farm for r in rows if r.farm}),
	}


@frappe.whitelist()
def scope_of(variety=None, farm=None, plan=None):
	"""What the dashboard is actually showing, and whether anyone chose it."""
	_guard()
	chosen = resolve_plan(variety, farm, plan)
	f = {"docstatus": ["<", 2]}
	if variety:
		f["variety"] = variety
	if farm:
		f["farm"] = farm
	total = frappe.db.count("Summer Flower Production Plan", f)
	p = frappe.db.get_value("Summer Flower Production Plan", chosen,
	                        ["name", "variety", "farm", "season", "docstatus",
	                         "workflow_state"], as_dict=True) if chosen else None
	return {
		"plan": chosen,
		"variety": p.variety if p else None,
		"farm": p.farm if p else None,
		"season": p.season if p else None,
		"state": (p.workflow_state if p else None),
		# Judged on variety and farm, not on the plan. The page resolves a plan for
		"implicit": bool(chosen and not (variety or farm)),
		"plans_in_scope": total,
		"asked": {"variety": variety or None, "farm": farm or None,
		          "plan": plan or None},
	}


@frappe.whitelist()
def demand_vs_production(variety=None, farm=None, plan=None):
	"""Weekly and monthly demand against planned production, plus plants and stems."""
	_guard()
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None}

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	# Only a committed order constrains anything, so only then is the stored
	committed = bool(p.tc_choice_committed)

	weeks = [
		{
			"label": f"{w.year}-W{w.week_no:02d}",
			"year": w.year, "week_no": w.week_no, "date": str(w.week_start_date),
			"demand": w.demand_stems or 0,
			"production": w.production_stems or 0,
			"variance": w.variance_stems or 0,
			# What the confirmed order can actually supply this week. Equal to
			"supplied": (cint(w.supplied_production_stems) if committed
			             else cint(w.production_stems)),
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
		# The register this plan answers, so the demand tab can act on it without
		"market_demand": p.market_demand,
		"period": f"{p.from_year}-W{p.from_week:02d} to {p.to_year}-W{p.to_week:02d}",
		"tc_committed": committed,
		"tc_plants_committed": cint(p.tc_plants_committed),
		"tc_order_date_committed": str(p.tc_order_date_committed or ""),
		"tc_cycles_committed": cint(p.tc_cycles_committed),
		"weeks": weeks,
		"months": months,
		"totals": {
			"demand_stems": p.total_demand_stems or 0,
			"production_stems": p.total_production_stems or 0,
			"variance_stems": p.total_variance_stems or 0,
			"coverage_pct": flt(p.coverage_pct),
			"supplied_stems": (cint(p.supplied_production_stems) if committed
			                   else cint(p.total_production_stems)),
			"supplied_coverage_pct": (flt(p.supplied_coverage_pct) if committed
			                          else flt(p.coverage_pct)),
			"weeks_in_deficit": p.weeks_in_deficit or 0,
			"plants_required": plants,
			"beds_required": p.new_beds_required or 0,
			"plantings_proposed": len(new_rows),
			"peak_weekly_sticking": p.peak_weekly_sticking or 0,
			"peak_weekly_sticking_planned": p.get("peak_weekly_sticking_planned") or 0,
			"peak_sticking_week": p.peak_sticking_week,
			"average_area_ha": flt(p.average_area_ha),
			"stems_per_plant_life": flt(v.total_stems_per_plant_life),
			"stems_per_ha_year": flt(v.stems_per_ha_year),
		},
	}


@frappe.whitelist()
def demand_vs_production_by_variety(farm=None, season=None):
	"""Every variety's demand and production in one payload, and their sum."""
	_guard()
	f = {"docstatus": ["<", 2]}
	if farm:
		f["farm"] = farm
	if season:
		f["season_start_year"] = season_year(season)
	rows = frappe.get_all("Summer Flower Production Plan", filters=f,
	                      fields=["name", "variety", "docstatus", "creation"],
	                      order_by="creation desc")

	# One plan per variety: the authoritative one where there is one, else the
	best = {}
	for r in rows:
		cur = best.get(r.variety)
		if cur is None or (r.docstatus == 1 and cur.docstatus != 1):
			best[r.variety] = r

	series, weeks, months = [], {}, {}
	for variety, r in sorted(best.items()):
		d = demand_vs_production(plan=r.name)
		if not d.get("plan"):
			continue
		t = d["totals"]
		# When this variety goes in the ground, and what has been settled about
		blocks = frappe.get_all(
			"Summer Flower Plan Block",
			filters={"parent": r.name, "parenttype": "Summer Flower Production Plan",
			         "is_new_planting": 1},
			fields=["planting_date", "beds", "plants", "block"])
		dates = sorted(b.planting_date for b in blocks if b.planting_date)
		prop = frappe.db.get_value(
			"Summer Flower Propagation Plan",
			{"production_plan": r.name, "docstatus": ["<", 2]},
			["name", "status", "first_sticking_date", "total_cuttings_required",
			 "cuttings_uncovered", "tc_plants_required", "tc_order_date"],
			as_dict=True)
		proc = frappe.db.get_value(
			"Summer Flower Procurement Plan",
			{"production_plan": r.name, "docstatus": ["<", 2]},
			["name", "status", "method", "entry_stage", "propagates_here",
			 "total_units_to_order", "first_order_by", "orders_late"],
			as_dict=True)
		series.append({
			"variety": variety, "plan": d["plan"], "farm": d["farm"],
			"status": d["status"],
			"demand_stems": cint(t["demand_stems"]),
			"production_stems": cint(t["production_stems"]),
			"coverage_pct": flt(t["coverage_pct"]),
			"plantings": len(blocks),
			"beds": sum(cint(b.beds) for b in blocks),
			"plants": sum(cint(b.plants) for b in blocks),
			"placed": sum(1 for b in blocks if b.block),
			"first_planting": str(dates[0]) if dates else None,
			"last_planting": str(dates[-1]) if dates else None,
			"propagation": (dict(prop, name=prop.name) if prop else None),
			"procurement": (dict(proc, name=proc.name) if proc else None),
			"weeks": d["weeks"], "months": d["months"],
		})
		for grain, bag in (("weeks", weeks), ("months", months)):
			for w in d[grain]:
				cell = bag.setdefault(w["label"], {"label": w["label"],
				                                   "production": 0, "demand": 0})
				# Sorted on when it happened, never on how it is written. A week
				cell["sort"] = (cint(w.get("year")), cint(w.get("month"))
				                or cint(w.get("week_no")))
				cell["production"] += cint(w.get("production"))
				cell["demand"] += cint(w.get("demand"))

	def ordered(bag):
		out = sorted(bag.values(), key=lambda c: c["sort"])
		for c in out:
			c["variance"] = c["production"] - c["demand"]
		return out

	tot_d = sum(x["demand_stems"] for x in series)
	tot_p = sum(x["production_stems"] for x in series)
	return {
		"varieties": [x["variety"] for x in series],
		"series": series,
		"weeks": ordered(weeks),
		"months": ordered(months),
		"totals": {
			"demand_stems": tot_d, "production_stems": tot_p,
			"coverage_pct": (tot_p / tot_d * 100) if tot_d else 0,
			"plans": len(series),
		},
	}


def _has_pool(v):
	"""Whether this crop's material passes through a standing pool."""
	return any(cint(r.get("is_standing")) for r in (v.get("material_route") or []))


def _direct_derivation(dv, v, peak_plants):
	"""The same question for a crop bought and planted: how big is the order."""
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			plants_to_plant,
		)

	entry = next((r.stage for r in (v.get("material_route") or [])
	              if cint(r.get("is_purchase"))), None)
	j = sourcing.journey(v, entry) if entry else None
	per_unit = flt((j or {}).get("plants_per_unit")) or 0
	to_plant = plants_to_plant(v, peak_plants) if peak_plants else 0
	units = int(math.ceil(peak_plants / per_unit)) if (peak_plants and per_unit) else 0
	unit = (entry or _("units")).lower()
	steps = [
		{"step": _("Peak weekly demand"),
		 "value": max((w["demand"] for w in dv["weeks"]), default=0),
		 "unit": "stems", "note": _("largest single week in the plan")},
		{"step": _("Stems per plant at flush 1"),
		 "value": v.flush_offsets()[0][1] if v.flush_schedule else 0,
		 "unit": "stems/plant", "note": _("first harvest only")},
		{"step": _("Plants to stand in the peak week"), "value": peak_plants,
		 "unit": "plants",
		 "note": _("what the week's demand needs growing, after grading loss")},
		{"step": _("To plant that week"), "value": to_plant, "unit": "plants",
		 "note": _("more than will stand: {0}% of what goes in establishes")
		 .format(flt(v.field_establishment_pct) or 100)},
		{"step": _("{0} to order").format(entry or _("Units")), "value": units,
		 "unit": unit,
		 "note": _("one {0} becomes {1} plants down this route")
		 .format(unit.rstrip("s"), round(per_unit, 4)) if per_unit
		 else _("the route does not say what one becomes")},
	]
	return {
		"plan": dv["plan"], "variety": dv["variety"], "farm": dv["farm"],
		"version": v.name, "steps": steps,
		# No pool, so nothing to say about mothers, pots or bench.
		"propagates": False, "entry_stage": entry, "unit": unit,
		"mothers_required": 0, "pots_required": 0, "bench_sqm": 0,
		"tc_plants": units, "tc_order_date": None, "tc_on_farm_date": None,
		"first_sticking_date": None, "tc_late": False, "build_up_cycles": 0,
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
	# Sized from the planned peak, so the derivation shows a real order for a plan
	peak_plants = t.get("peak_weekly_sticking_planned") or t["peak_weekly_sticking"] or 0
	# A crop with no pool is not a motherstock story and must not be told as one.
	if not _has_pool(v):
		return _direct_derivation(dv, v, peak_plants)
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
		 "note": f"x{flt(v.cuttings_per_plant_required, 3)} for rooting and field "
		         f"loss. The cutting reject is a grading loss on stems and is "
		         f"taken off the yield, not added here"},
		{"step": "Mother plants required", "value": mothers, "unit": "plants",
		 "note": f"at {per_week:g} cutting per plant per week"},
	]
	# The TC order is the long pole in this whole chain -- 22 to 26 weeks of lead
	tc = {"tc_plants": 0, "tc_order_date": "", "tc_on_farm_date": "",
	      "first_sticking_date": "", "tc_source": None, "tc_cost": 0,
	      "ramp_weeks": 0, "full_capacity_date": "", "lost_to_ramp": 0,
	      # The build-up these figures assume. Without it a caller seeding a
	      "build_up_cycles": 0}
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
			if cint(r.mother_plants_required) != mothers:
				steps[-1]["note"] += _(
					" — {0} says {1} after netting off standing motherstock"
				).format(r.name, cint(r.mother_plants_required))
				steps[-1]["value"] = cint(r.mother_plants_required)
			mothers = cint(r.mother_plants_required)
	elif mothers:
		# No propagation plan yet, so size the order the same way one would: run an
		probe = frappe.new_doc("Summer Flower Motherstock Batch")
		probe.variety, probe.farm, probe.protocol = dv["variety"], dv["farm"], v.name
		probe.peak_weekly_cuttings = cuttings
		# cuttings is already cuttings_for_plants(peak) -- rooting and field loss and
		probe.apply_losses = 0
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

	# The requirement and the order are different numbers and both belong on the
	required = int(math.ceil(v.tc_plants_for(mothers, None, with_loss=False))) if mothers else 0
	mf = v.multiplication_factor()
	factor_txt = ("%g" % mf)
	steps.append({
		"step": "TC plants required", "value": required, "unit": "TC plants",
		"note": "%s mother plants / %s, because %s multiplication cycles turn one "
		        "TC plant into %s plants"
		        % (mothers, factor_txt, cint(v.max_multiplication_cycles), factor_txt),
	})
	if flt(v.tc_order_loss_pct):
		steps.append({
			"step": "TC order loss allowance", "value": flt(v.tc_order_loss_pct),
			"unit": "%",
			"note": "TC plants lost between the lab and the bench, so the order is "
			        "the requirement / %.2f" % (1 - flt(v.tc_order_loss_pct) / 100),
		})
	steps.append({
		"step": "TC to order", "value": tc["tc_plants"], "unit": "TC",
		"note": ("order by %s, on farm %s, first cut %s"
		         % (tc["tc_order_date"] or "?", tc["tc_on_farm_date"] or "?",
		            tc["first_sticking_date"] or "?")) if tc["tc_plants"]
		        else "nothing sized yet",
	})
	tc["tc_late"] = bool(tc["tc_order_date"]
	                     and getdate(tc["tc_order_date"]) < getdate(nowdate()))
	tc["build_up_cycles"] = (cint(dv.get("tc_cycles_committed"))
	                         if dv.get("tc_committed")
	                         else cint(v.max_multiplication_cycles))

	out = {
		"plan": dv["plan"], "variety": dv["variety"], "farm": dv["farm"],
		"version": v.name, "steps": steps, "propagates": True,
		"mothers_required": mothers,
		"pots_required": int(round(mothers / (v.plants_per_pot or 1))) if mothers else 0,
		"bench_sqm": round(mothers / v.plants_per_sqm_bench, 1)
		if v.plants_per_sqm_bench else 0,
	}
	out.update(tc)
	return out


from upande_summer_flowers.summer_flowers.doctype.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
	tc_order_by_date,
)


def _prop_filters(plan):
	"""How to find the propagation plan for a production plan."""
	row = frappe.db.get_value("Summer Flower Production Plan", plan,
	                          ["variety", "season_start_year"], as_dict=True)
	if not row:
		return {"production_plan": plan, "status": ["!=", "Rejected"]}
	return {"variety": row.variety,
	        "season_start_year": season_year(row.season_start_year),
	        "status": ["!=", "Rejected"]}


def sizing_peak(p):
	"""The sticking week the TC order is sized from, and what it counts."""
	planned = cint(p.get("peak_weekly_sticking_planned"))
	placed = cint(p.get("peak_weekly_sticking"))
	if planned:
		return (planned,
		        p.get("peak_sticking_week_planned") or p.get("peak_sticking_week"),
		        "planned")
	return placed, p.get("peak_sticking_week"), "placed"


def _first_sticking_for(plan, placed_only=False):
	"""Earliest sticking week the plan proposes, as a date."""
	extra = "and ifnull(not_placed, 0) = 0" if placed_only else ""
	row = frappe.db.sql("""select sticking_year y, sticking_week w
		from `tabSummer Flower Plan Block`
		where parent = %s and is_new_planting = 1 {0}
		  and ifnull(sticking_year, 0) > 0
		order by sticking_year asc, sticking_week asc limit 1""".format(extra),
		(plan,), as_dict=True)
	return iso_monday(row[0].y, row[0].w) if row else None



# ---------------------------------------------------------------------------

def _plants_per_bought_unit(plan_doc):
	"""Plants standing per unit bought, the way the materials card counts it."""
	from upande_summer_flowers.summer_flowers import root_line

	try:
		m = root_line.arrival_plan(plan_doc)
	except Exception:
		frappe.clear_last_message()
		return 0.0
	units = cint(m.get("tc_units_total"))
	plants = cint(m.get("tc_total"))
	if units and plants:
		return flt(plants) / flt(units)
	return flt(m.get("tc_plants_per_unit")) or 0.0


def _entry_order_date(plan_doc, version):
	"""When the season's first consignment has to be ordered."""
	return (getdate(plan_doc.tc_order_by_date) if plan_doc.get("tc_order_by_date")
	        else tc_order_by_date(version, _first_sticking_for(plan_doc.name)))


def _tc_production(plan_doc, version, tc_qty, order_date, divert_weeks=0,
                   max_bench_sqm=None, week_overrides=None, schedule=None,
                   direct=False, plants_per_unit=0):
	"""Roll a TC quantity all the way through to stems against demand."""
	from upande_summer_flowers.summer_flowers import lifecycle_sim as ls

	v = version
	offsets = v.flush_offsets()
	if not offsets:
		return None

	grid = week_sequence(plan_doc.from_year, plan_doc.from_week,
	                     cint(plan_doc.weeks_covered))
	index = {(y, w): i for i, (y, w, _d) in enumerate(grid)}
	demand_map = {(r.year, r.week_no): cint(r.demand_stems)
	              for r in frappe.get_all(
	                  "Summer Flower Demand Week",
	                  filters={"parent": plan_doc.market_demand},
	                  fields=["year", "week_no", "demand_stems"])
	              if (r.year, r.week_no) in index}

	# Weekly cuttings the farm can actually take, from the pool this order builds.
	from upande_summer_flowers.summer_flowers.propagation_solver import TC_IS_ONE_MOTHER

	p = ls.params_from_version(v.name, TC_IS_ONE_MOTHER)
	# Overrides arrive keyed by the week a grower reads -- "2026-W06" -- and the
	overrides = {}
	for key, value in (week_overrides or {}).items():
		try:
			y, w = str(key).split("-W")
			sw = (getdate(iso_monday(int(y), int(w))) - getdate(order_date)).days // 7
		except Exception:
			continue
		if sw >= 0:
			overrides[sw] = cint(value)

	last_stick = max(
		(iso_monday(cint(b.sticking_year), cint(b.sticking_week))
		 for b in plan_doc.plan_blocks
		 if cint(b.is_new_planting) and cint(b.sticking_year) and cint(b.sticking_week)),
		default=None)
	span = cint(plan_doc.weeks_covered)
	if last_stick:
		span = max(span, ((getdate(last_stick) - getdate(order_date)).days // 7) + 4)
	# The diversion is weeks, not a standing percentage: the whole cut goes back
	first_cut_sw = (cint(p["supplier_lead_weeks"])
	                + (cint(p.get("tc_to_first_cut_weeks"))
	                   or cint(p["ms_establishment_weeks"])))
	# Counted before the diversion window is added, because those are not somebody's
	typed_overrides = len(overrides)
	for i in range(cint(divert_weeks)):
		overrides.setdefault(first_cut_sw + i, 0)
	if direct:
		# No motherstock on this route: nothing is gathered and nothing is
		# multiplied. The order is handed to the plantings in sticking order,
		# each taking what its own consignment delivers. Running the pool
		# simulation here produced a season of zeros for a plan that works.
		# Capacity is counted in the material the field is handed, which is what
		# cuttings_for_plants() asks for below -- so the pool IS the order.
		pool = cint(tc_qty)
		rows = []
		for b in sorted(
				[x for x in plan_doc.plan_blocks
				 if cint(x.is_new_planting) and cint(x.sticking_year)
				 and cint(x.sticking_week)],
				key=lambda x: (cint(x.sticking_year), cint(x.sticking_week))):
			want = v.cuttings_for_plants(
				cint(b.get("plants_from_demand")) or cint(b.plants))
			take = min(pool, want)
			pool -= take
			rows.append({"year": cint(b.sticking_year), "week_no": cint(b.sticking_week),
			             "to_farm": take, "total_cap": take, "base_cap": take,
			             "prop_cap": 0})
		for r in rows:
			k = (r["year"], r["week_no"])
			if k in (week_overrides or {}):
				r["to_farm"] = cint((week_overrides or {})[k])
		sim = {"rows": rows, "generations": 0, "num_cycles": 0, "prop_pool_rows": []}
	elif schedule is not None:
		# The peak-week line, already worked out, rather than a second simulation
		rows = []
		for r in schedule.get("weeks_table") or []:
			y, w = getdate(r["week_start"]).isocalendar()[:2]
			cut = cint(r.get("cuttings_cut"))
			rows.append({"year": y, "week_no": w,
			             "to_farm": cint(r.get("to_field")),
			             "total_cap": cut, "base_cap": cut, "prop_cap": 0})
		# A typed week still wins over the line, which is the point of the card.
		by_key = {}
		for key, value in (week_overrides or {}).items():
			try:
				yy, ww = str(key).split("-W")
				by_key[(int(yy), int(ww))] = cint(value)
			except Exception:
				continue
		for r in rows:
			k = (r["year"], r["week_no"])
			if k in by_key:
				r["to_farm"] = by_key[k]
		typed_overrides = len(by_key)
		sim = {"rows": rows,
		       "generations": len(schedule.get("generations") or []),
		       "num_cycles": cint(schedule.get("cycles")),
		       "prop_pool_rows": []}
	else:
		sim = ls.simulate(p, cint(tc_qty), order_date,
		                  farm_overrides=overrides,
		                  max_bench_sqm=max_bench_sqm,
		                  horizon_weeks=span,
		                  divert_until_sw=first_cut_sw + cint(divert_weeks))
	capacity = {}
	for r in sim["rows"]:
		capacity[(r["year"], r["week_no"])] = capacity.get(
			(r["year"], r["week_no"]), 0) + cint(r["to_farm"])

	production = {k: 0 for k in index}
	standing = 0
	for b in plan_doc.plan_blocks:
		if cint(b.is_new_planting):
			continue
		# Already in the ground: no cutting is needed for it, so no TC decision can
		if not b.existing_planting:
			continue
		from upande_summer_flowers.summer_flowers.doctype.planting_calendar \
			.planting_calendar import production_by_week
		pl = frappe.get_doc("Planting Calendar", b.existing_planting)
		for (y, w), stems in production_by_week(pl).items():
			if (y, w) in index:
				production[(y, w)] += stems
				standing += stems

	# New plantings draw on their sticking week, earliest first.
	rows = sorted(
		[b for b in plan_doc.plan_blocks
		 if cint(b.is_new_planting) and not cint(b.get("not_placed"))
		 and cint(b.sticking_year) and cint(b.sticking_week)],
		key=lambda b: (cint(b.sticking_year), cint(b.sticking_week)))

	# The first week any pool built by this order can cut. A planting whose
	first_cut = min((( r["year"], r["week_no"]) for r in sim["rows"]
	                 if cint(r["total_cap"])), default=None)

	total_cap_at = {}
	base_cap_at = {}
	prop_cap_at = {}
	for r in sim["rows"]:
		k = (r["year"], r["week_no"])
		total_cap_at[k] = total_cap_at.get(k, 0) + cint(r["total_cap"])
		base_cap_at[k] = base_cap_at.get(k, 0) + cint(r["base_cap"])
		prop_cap_at[k] = prop_cap_at.get(k, 0) + cint(r["prop_cap"])

	left = dict(capacity)
	stick_rows = {}
	full = trimmed = dropped = 0
	dropped_too_early = 0
	plants_wanted = plants_stuck = 0
	detail = []
	supplied_by_row = {}
	for b in rows:
		key = (cint(b.sticking_year), cint(b.sticking_week))
		# What the DEMAND asked for, not what a previous trim left behind. Walking
		want_plants = cint(b.get("plants_from_demand")) or cint(b.plants)
		plants_wanted += want_plants
		sr = stick_rows.setdefault(key, {
			"label": "%s-W%02d" % key,
			"week_start_date": str(iso_monday(*key)),
			"capacity": total_cap_at.get(key, 0),
			"base_capacity": base_cap_at.get(key, 0),
			# Capacity that exists this week only because cuttings were diverted
			"returned_capacity": prop_cap_at.get(key, 0),
			"to_farm": capacity.get(key, 0),
			"to_prop": total_cap_at.get(key, 0) - capacity.get(key, 0),
			"overridden": 1 if (
				(getdate(iso_monday(*key)) - getdate(order_date)).days // 7
			) in overrides else 0,
			"plantings": 0, "plants_wanted": 0, "plants_stuck": 0,
			"cuttings_needed": 0, "blocks": [],
		})
		sr["plantings"] += 1
		sr["plants_wanted"] += want_plants
		if b.block:
			sr["blocks"].append(b.block)
		need = v.cuttings_for_plants(want_plants)
		sr["cuttings_needed"] += need
		have = left.get(key, 0)
		if have <= 0:
			supplied_by_row[b.name] = 0
			dropped += 1
			too_early = bool(first_cut and key < first_cut)
			if too_early:
				dropped_too_early += 1
			detail.append({"stick": "%s-W%02d" % key, "block": b.block,
			               "wanted": want_plants, "got": 0,
			               "state": "before first cut" if too_early else "dropped"})
			continue
		if have >= need:
			got = want_plants
			left[key] = have - need
			full += 1
			state = "full"
		else:
			# Partial: as many plants as the cuttings allow.
			per_plant = (need / want_plants) if want_plants else 1
			got = int(have / per_plant) if per_plant else 0
			left[key] = 0
			if got <= 0:
				supplied_by_row[b.name] = 0
				dropped += 1
				detail.append({"stick": "%s-W%02d" % key, "block": b.block,
				               "wanted": want_plants, "got": 0, "state": "dropped"})
				continue
			trimmed += 1
			state = "trimmed"
		supplied_by_row[b.name] = got
		plants_stuck += got
		sr["plants_stuck"] += got
		if state != "full":
			detail.append({"stick": "%s-W%02d" % key, "block": b.block,
			               "wanted": want_plants, "got": got, "state": state})

		# Fold this planting's flushes in, at the plants that were actually stuck.
		pdate = getdate(b.planting_date) if b.planting_date else None
		if not pdate:
			continue
		uproot = pdate + datetime.timedelta(weeks=cint(v.total_weeks_in_ground))
		for off, spp in offsets:
			hd = pdate + datetime.timedelta(weeks=off)
			if hd > uproot:
				break
			hy, hw = iso_year_week(hd)
			if (hy, hw) in index:
				production[(hy, hw)] += int(round(spp * got))

	weeks = []
	total_prod = total_dem = deficit_weeks = unmet = 0
	for (y, w, _d) in grid:
		dem = demand_map.get((y, w), 0)
		prod = production.get((y, w), 0)
		total_prod += prod
		total_dem += dem
		if prod < dem:
			deficit_weeks += 1
			unmet += dem - prod
		weeks.append({"year": y, "week_no": w, "label": "%s-W%02d" % (y, w),
		              "demand": dem, "production": prod, "variance": prod - dem,
		              "capacity": capacity.get((y, w), 0)})

	return {
		"tc_qty": cint(tc_qty),
		"order_date": str(order_date),
		"weeks": weeks,
		# Per planting, how many plants the agreed order can actually stick. The
		"supplied_by_row": supplied_by_row,
		"production_stems": total_prod,
		"demand_stems": total_dem,
		"standing_stems": standing,
		"coverage_pct": round(total_prod * 100.0 / total_dem, 2) if total_dem else 0,
		"weeks_in_deficit": deficit_weeks,
		"unmet_stems": unmet,
		"plantings_full": full,
		"plantings_trimmed": trimmed,
		"plantings_dropped": dropped,
		"dropped_too_early": dropped_too_early,
		"dropped_no_capacity": dropped - dropped_too_early,
		"first_cut_week": "%s-W%02d" % first_cut if first_cut else None,
		"plants_wanted": plants_wanted,
		"plants_stuck": plants_stuck,
		"generations": sim.get("generations"),
		"num_cycles": sim.get("num_cycles"),
		"detail": detail[:40],
		"sticking": [stick_rows[k] for k in sorted(stick_rows)],
		"overrides": typed_overrides,
		"prop_returns": sim.get("prop_pool_rows", []),
		"establishment_weeks": cint(p.get("ms_establishment_weeks")),
		"tc_to_first_cut_weeks": cint(p.get("tc_to_first_cut_weeks")),
		"ramp_weeks": len(p.get("ramp") or []),
	}


def _solve_tc_for_demand(plan_doc, version, order_date, target_pct=100.0,
                         tolerance_pct=10.0, ceiling=None):
	"""Smallest TC order whose production lands within tolerance of demand."""
	lo, hi = 0, cint(ceiling) or 200_000
	floor_pct = target_pct - flt(tolerance_pct)
	best = None
	# Is the ceiling even enough? If not, say so rather than returning it silently.
	top = _tc_production(plan_doc, version, hi, order_date)
	if not top:
		return None
	if top["coverage_pct"] < floor_pct:
		top["solved"] = False
		top["reason"] = "even %s TC plants only reaches %.1f%%" % (
			f"{hi:,}", top["coverage_pct"])
		return top
	for _ in range(18):
		mid = (lo + hi) // 2
		if mid == lo:
			break
		res = _tc_production(plan_doc, version, mid, order_date)
		if res["coverage_pct"] >= floor_pct:
			best = res
			hi = mid
		else:
			lo = mid
	best = best or top
	best["solved"] = True
	return best


def _cutting_weeks(version, cycles):
	"""Weeks left to cut from a pool reached in `cycles`, or None if unknowable."""
	life = cint(version.motherstock_life_weeks)
	est = cint(version.weeks_tc_to_first_cut())
	if not life or (cint(cycles) > 1 and not est):
		return None
	return max(0, life - est * max(0, cint(cycles) - 1))


@frappe.whitelist()
def tc_purchase(plan=None, variety=None, farm=None, tc_qty=None, tolerance_pct=10,
                order_date=None, cycles=None):
	"""What to buy on the first TC order, and what buying differently does."""
	_guard()
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None}

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	tol = flt(tolerance_pct) or 10.0

	peak_plants, peak_week, peak_basis = sizing_peak(p)
	cuttings = v.cuttings_for_plants(peak_plants) if peak_plants else 0
	per_week = flt(v.cuttings_per_plant_per_week) or 1.0
	mothers = int(math.ceil(cuttings / per_week)) if cuttings else 0
	# The three levers, each falling back to what the plan is running on. Asking for
	cycles = cint(cycles) if cycles not in (None, "") else (
		cint(p.tc_cycles_committed) if p.tc_choice_committed
		else cint(v.max_multiplication_cycles))
	recommended = int(math.ceil(v.tc_plants_for(mothers, cycles))) if mothers else 0

	# The propagation plan nets off standing motherstock, so where one exists its
	prop = frappe.get_all(
		"Summer Flower Propagation Plan",
		filters=_prop_filters(plan),
		fields=["name", "tc_plants_required", "mother_plants_required",
		        "tc_order_date", "tc_on_farm_date", "first_sticking_date",
		        "full_capacity_date", "ramp_weeks", "cuttings_uncovered",
		        "total_cuttings_required", "tc_cost", "status",
		        "plants_short", "stems_at_risk", "total_plants_to_stick"],
		order_by="creation desc", limit=1)
	prop = prop[0] if prop else None

	chosen = cint(tc_qty) or (cint(prop.tc_plants_required) if prop else recommended)
	factor = v.multiplication_factor(cycles)
	pool_for = lambda tc: int(round(cint(tc) * factor))
	cover_of_peak = (pool_for(chosen) * per_week / cuttings * 100) if cuttings else 0

	# The order date decides which weeks the pool can supply at all, so it comes
	if order_date:
		order_date = getdate(order_date)
	elif p.tc_choice_committed and p.tc_order_date_committed:
		order_date = getdate(p.tc_order_date_committed)
	elif prop and prop.tc_order_date and cycles == cint(v.max_multiplication_cycles):
		order_date = getdate(prop.tc_order_date)
	else:
		order_date = tc_order_by_date(v, _first_sticking_for(plan), cycles=cycles)

	# What this quantity actually grows, and the smallest quantity that lands the
	built = solved = None
	if order_date:
		built = _tc_production(p, v, chosen, order_date)
		solved = _solve_tc_for_demand(p, v, order_date, tolerance_pct=tol,
		                              ceiling=max(recommended * 40, 200_000))
	# When no order size lands the demand, the reader needs to know whether buying
	binding = None
	if solved and not solved.get("solved"):
		if cint(p.plantings_not_placed):
			binding = _(
				"Buying more TC plants cannot close this. {0} of the {1} proposed "
				"plantings have no block free for their whole life, so {2} stems of "
				"demand have nowhere to grow -- blocks are the constraint here, not "
				"tissue culture."
			).format(cint(p.plantings_not_placed),
			         len([b for b in p.plan_blocks if b.is_new_planting]),
			         cint(p.unmet_stems))
		else:
			binding = _(
				"No order size within the ceiling lands the demand, and every proposed "
				"planting has a block, so the gap is in the demand's timing rather "
				"than in the TC plants: the earliest sticking weeks are too close to "
				"today for any order to reach them.")

	base = cint(solved["tc_qty"]) if (solved and solved.get("solved")) else (
		cint(prop.tc_plants_required) if prop else recommended)

	return {
		"order_date_used": str(order_date or ""),
		"built": built,
		"solved": solved,
		"tc_for_demand": cint(solved["tc_qty"]) if solved else 0,
		"tc_for_demand_solved": bool(solved and solved.get("solved")),
		"tc_for_demand_coverage": flt(solved["coverage_pct"]) if solved else 0,
		"tc_for_demand_reason": (solved or {}).get("reason"),
		"binding_constraint": binding,
		"built_coverage_pct": flt(built["coverage_pct"]) if built else None,
		"built_production": cint(built["production_stems"]) if built else 0,
		"built_unmet": cint(built["unmet_stems"]) if built else 0,
		"built_deficit_weeks": cint(built["weeks_in_deficit"]) if built else 0,
		"built_full": cint(built["plantings_full"]) if built else 0,
		"built_trimmed": cint(built["plantings_trimmed"]) if built else 0,
		"built_dropped": cint(built["plantings_dropped"]) if built else 0,
		"built_dropped_too_early": cint(built["dropped_too_early"]) if built else 0,
		"built_first_cut_week": (built or {}).get("first_cut_week"),
		"built_plants": cint(built["plants_stuck"]) if built else 0,
		"built_plants_wanted": cint(built["plants_wanted"]) if built else 0,
		"plan": plan, "variety": p.variety, "farm": p.farm, "version": v.name,
		"tolerance_pct": tol,
		"peak_plants": peak_plants,
		"peak_week": peak_week,
		# Which peak the order was sized from, so the panel can say so rather than
		"peak_basis": peak_basis,
		"peak_plants_placed": cint(p.peak_weekly_sticking),
		"peak_cuttings": cuttings,
		"mothers_for_peak": mothers,
		"build_up_cycles": cycles,
		# What multiplying costs. The line is renewed from tissue culture on
		"line_life_weeks": cint(v.motherstock_life_weeks),
		"cycle_cost_weeks": cint(v.weeks_tc_to_first_cut()),
		# None, not zero, where the protocol has not said how long the line lives or
		"cutting_weeks": _cutting_weeks(v, cycles),
		"cutting_weeks_by_cycles": [
			{"cycles": c,
			 "factor": round(v.multiplication_factor(c), 2),
			 "tc": int(math.ceil(v.tc_plants_for(mothers, c))) if mothers else 0,
			 "weeks": _cutting_weeks(v, c)}
			for c in range(1, max(5, cycles + 2))
		],
		# What the plan is actually committed to, so the dashboard can show a tried
		"sourcing": ({
			"propagation_plan": prop.name,
			"plants_to_stick": cint(prop.total_plants_to_stick),
			"plants_short": cint(prop.plants_short),
			"stems_at_risk": cint(prop.stems_at_risk),
			"cuttings_uncovered": cint(prop.cuttings_uncovered),
			"cuttings_required": cint(prop.total_cuttings_required),
		} if prop else None),
		"committed": bool(p.tc_choice_committed),
		"committed_tc": cint(p.tc_plants_committed),
		"committed_order_date": str(p.tc_order_date_committed or ""),
		"committed_cycles": cint(p.tc_cycles_committed),
		"protocol_cycles": cint(v.max_multiplication_cycles),
		"plan_submitted": p.docstatus == 1,
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
		"in_band": bool(built and abs(flt(built["coverage_pct"]) - 100) <= tol),
		"band_low": int(round(base * (1 - tol / 100))) if base else 0,
		"band_high": int(round(base * (1 + tol / 100))) if base else 0,
		"rate_per_plantlet": flt(_rate_for(chosen)),
		"cost": round(flt(_rate_for(chosen)) * chosen, 2),
		"plan_coverage_pct": flt(p.coverage_pct),
	}


@frappe.whitelist()
def plan_whatif(plan=None, variety=None, farm=None, tc_qty=None, week_overrides=None,
                multiplications=None, divert_weeks=None, order_date=None,
                to_prop_pct=None, cycles=None):
	"""Try a different order and see what it does to the plan's stems."""
	_guard()
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None}
	if isinstance(week_overrides, str):
		week_overrides = frappe.parse_json(week_overrides or "{}")

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	prop = frappe.get_all(
		"Summer Flower Propagation Plan",
		filters=_prop_filters(plan),
		fields=["name", "tc_plants_required", "tc_order_date",
		        "mother_plants_required"],
		order_by="creation desc", limit=1)
	prop = prop[0] if prop else None

	from upande_summer_flowers.summer_flowers import propagation_solver as ps
	from upande_summer_flowers.summer_flowers import sourcing

	# Does this crop multiply at all? A route with no standing stage has no
	# mother block to gather from, so the cuttings engine answers every week
	# with nothing and the card reported 0% for a plan that covers its demand.
	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	multiplies = bool(entry and sourcing.standing_stage(v, entry))

	# The line as the solver works it out, for the multiplication being asked for.
	agreed = frappe.db.get_value(
		"Summer Flower Motherstock Plan", {"production_plan": plan},
		["tc_to_order", "multiplications"], as_dict=True)
	if agreed and multiplications in (None, "") and not cint(tc_qty):
		multiplications = cint(agreed.multiplications)
		tc_qty = cint(agreed.tc_to_order)

	mult = multiplications if multiplications not in (None, "") else None
	if not multiplies:
		# Bought and planted: the only lever is how many to buy.
		per_unit = _plants_per_bought_unit(p)
		season_order = cint(p.tc_plants_to_order) or cint(
			round(cint(p.new_plants_required) / (per_unit or 1)))
		chosen = cint(tc_qty) if cint(tc_qty) else season_order
		built = _tc_production(p, v, chosen, _entry_order_date(p, v),
		                       week_overrides=week_overrides,
		                       direct=True, plants_per_unit=per_unit)
		return {
			"plan": plan, "variety": p.variety, "farm": p.farm, "version": v.name,
			"status": p.workflow_state or p.status,
			"order_date": str(_entry_order_date(p, v) or ""),
			"tc_qty": chosen, "recommended_tc": season_order,
			# Said plainly, so the card can drop the lever instead of showing
			# it stuck at a number nothing acts on.
			"multiplies": 0,
			"route_reason": decided.get("reason"),
			"entry_stage": entry,
			"plants_per_unit": per_unit,
			"multiplications": 0, "divert_weeks": 0, "generations": 0,
			"max_multiplications": 0, "farm_max_multiplications": 0,
			"multiplications_asked": 0, "suggested_multiplications": 0,
			"sends_made": 0, "weeks_met": 0, "weeks_required": 0,
			"covers": 1 if built and not cint(built.get("unmet_stems")) else 0,
			"shortfall": 0,
			"propagation_plan": prop.name if prop else None,
			"current": {"tc_qty": cint(prop.tc_plants_required) if prop else 0,
			            "order_date": (str(prop.tc_order_date)
			                           if prop and prop.tc_order_date else None),
			            "divert_weeks": 0},
			"baseline": {
				"coverage_pct": flt(p.coverage_pct),
				"production_stems": cint(p.total_production_stems),
				"demand_stems": cint(p.total_demand_stems),
				"weeks_in_deficit": cint(p.weeks_in_deficit),
				"plants": cint(p.new_plants_required),
			},
			"built": built,
		}
	sizing = ps.peak_sizing(p, cycles=mult)
	if not sizing:
		return {"plan": plan, "error": "No sticking weeks to plan cuttings for."}
	sched = ps.peak_schedule(p, sizing, cycles=mult, tc=tc_qty)
	chosen_line = {
		"tc": (sched or {}).get("tc"),
		"multiplications": (sched or {}).get("cycles"),
		"order_by": (sched or {}).get("order_by"),
		"first_cut_date": (sched or {}).get("first_cut_date"),
		"line_end_date": (sched or {}).get("line_end_date"),
		"peak_pool": (sched or {}).get("peak_pool"),
		"weeks_met": (sched or {}).get("weeks_met"),
		"weeks": (sched or {}).get("weeks"),
		"generations": (sched or {}).get("generations") or [],
	}
	rec_line = sizing["options"][sizing["suggested"]]
	mult = cint(chosen_line.get("multiplications") if mult is None else mult)

	# An asked-for date wins; otherwise the one the diversion implies, which is what
	asked_date = getdate(order_date) if order_date else None
	if asked_date:
		order_date = asked_date
	elif chosen_line.get("order_by"):
		order_date = getdate(chosen_line["order_by"])
	elif prop and prop.tc_order_date:
		order_date = prop.tc_order_date
	else:
		order_date = tc_order_by_date(v, _first_sticking_for(plan))
	if not order_date:
		return {"plan": plan, "error": "No sticking weeks to plan cuttings for."}

	# What to buy for the cycles being ASKED for, which is the whole point of the
	recommended = cint(rec_line.get("buy") or chosen_line.get("tc") or 0)
	chosen = cint(tc_qty) if cint(tc_qty) else cint(chosen_line.get("tc") or recommended)

	built = _tc_production(p, v, chosen, order_date, week_overrides=week_overrides,
	                       schedule=sched)
	# The plan as it stands, for comparison: what changing anything is measured
	return {
		"plan": plan, "variety": p.variety, "farm": p.farm, "version": v.name,
		"status": p.workflow_state or p.status,
		"order_date": str(order_date),
		"tc_qty": chosen,
		"multiplications": mult,
		# Kept under its old name as well, because the card and the saved plan
		"divert_weeks": mult,
		"generations": len(chosen_line.get("generations") or []),
		"weeks_met": cint(chosen_line.get("weeks_met")),
		"weeks_required": cint(chosen_line.get("weeks")),
		"line_end_date": chosen_line.get("line_end_date"),
		"first_cut_date": chosen_line.get("first_cut_date"),
		# The protocol's cap where it states one, not the farm's habitual four.
		"max_multiplications": (cint(sizing.get("max_cycles"))
		                        or ps.max_multiplications()),
		"farm_max_multiplications": ps.max_multiplications(),
		"multiplications_asked": mult,
		"suggested_multiplications": cint(sizing["suggested"]),
		"covers": 1 if (sched or {}).get("covers") else 0,
		"shortfall": cint((sched or {}).get("shortfall")),
		"sends_made": cint((sched or {}).get("sends_made")),
		# For the cycles asked for, not for the plan's. What the plan is running on
		"recommended_tc": cint(recommended),
		"propagation_plan": prop.name if prop else None,
		# What the plan is running on now, so the three levers can be shown as
		"current": {
			"tc_qty": cint(prop.tc_plants_required) if prop else 0,
			"order_date": str(prop.tc_order_date) if prop and prop.tc_order_date else None,
			"divert_weeks": cint(frappe.db.get_value(
				"Summer Flower Motherstock Plan",
				{"production_plan": plan}, "multiplications") or 0),
		},
		# What the order actually becomes, week by week, rather than the order times
		"mother_plants_from_order": cint(chosen_line.get("peak_pool")),
		"baseline": {
			"coverage_pct": flt(p.coverage_pct),
			"production_stems": cint(p.total_production_stems),
			"demand_stems": cint(p.total_demand_stems),
			"weeks_in_deficit": cint(p.weeks_in_deficit),
			"plants": cint(p.new_plants_required),
		},
		"built": built,
	}


@frappe.whitelist()
def tc_options(version, target_pool, need_by=None, field_pct=0, max_rounds=6):
	"""TC order size against how early it has to arrive."""
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
	"""Run the build-up and return the timeline, ledger and space curve."""
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
	}
	# What the released plants turn into on the ground.
	beds = (res["field_plants_released"] / p["plants_per_bed"]) if p.get("plants_per_bed") else 0
	res["field_effect"] = {
		"plants": res["field_plants_released"],
		"beds": round(beds, 1),
		"net_area_ha": round(beds * flt(p.get("sqm_net_per_bed")) / 10_000, 4),
		"stems_over_life": int(round(
			res["field_plants_released"] * flt(p.get("total_stems_per_plant_life"))
		)),
	}
	return res


@frappe.whitelist()
def planting_plan(plan=None, variety=None, farm=None):
	"""The planting plan: what to stick, when to plant, and where it lands."""
	_guard()
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None, "plantings": [], "occupancy": [], "totals": {}}

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	life_weeks = v.total_weeks_in_ground or 0

	# What each planting cuts INSIDE the plan's weeks, from the plan's own
	# matrix. A lifetime total answers a question nobody asked of a season.
	from upande_summer_flowers.summer_flowers.root_line import _weekly_cut

	in_season = {i: sum(cint(q) for q in (wk or {}).values())
	             for i, wk in (_weekly_cut(p) or {}).items()}

	plantings = []
	for i, b in enumerate(p.plan_blocks):
		plantings.append({
			"idx": b.idx,
			"season_stems": cint(in_season.get(i, 0)),
			"is_new": bool(b.is_new_planting),
			"block": b.block,
			"existing_planting": b.existing_planting,
			"beds": b.beds or 0,
			"plants": b.plants or 0,
			# What goes in the ground, which is what the farm orders, carries and
			# plants. The standing figure is what is left of it once established.
			"plants_to_plant": cint(b.get("plants_to_plant")) or cint(b.plants or 0),
			"cuttings": v.cuttings_for_plants(b.plants or 0) if b.plants else 0,
			"stick": f"{b.sticking_year}-W{b.sticking_week:02d}"
			if b.sticking_year and b.sticking_week else None,
			"plant": f"{b.planting_year}-W{b.planting_week:02d}"
			if b.planting_year and b.planting_week else None,
			# Year and week as numbers too, so the dashboard can filter on a period
			"planting_year": b.planting_year,
			"planting_week": b.planting_week,
			"sticking_year": b.sticking_year,
			"sticking_week": b.sticking_week,
			"planting_date": str(b.planting_date) if b.planting_date else None,
			"pinch_date": str(b.pinch_date) if b.pinch_date else None,
			# The other dates the plan commits to, so a calendar view does not have
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
			"net_area_ha": flt(b.net_area_ha),
			"lifetime_stems": b.lifetime_stems or 0,
			"below_minimum": bool(b.below_minimum),
			"in_past": bool(b.planting_in_past),
			"unallocated": bool(b.is_new_planting and not b.block),
			"not_placed": bool(b.get("not_placed")),
			"notes": b.notes,
			# The child row's own name, so the allocation control can assign a block
			"row": b.name,
			"is_new": bool(b.is_new_planting),
		})

	# Occupancy windows, from the plan's allocated rows plus anything already on
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

	# Nothing drawn yet: show what the demand asks for, so the requirement is
	# on the page while the supply is still being settled. Marked as not drawn,
	# because it is not a programme until the houses have been agreed.
	awaiting = 0
	if not any(x["is_new"] for x in plantings):
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
			requirement_rows,
		)

		asked = requirement_rows(p)
		if asked:
			awaiting = 1
			plantings.extend(asked)

	new_rows = [x for x in plantings if x["is_new"]]
	blocks_available = frappe.db.count("Block", {
		"custom_is_summer_flower_block": 1, **({"farm": p.farm} if p.farm else {})
	})
	# Every tab is showing this plan's stored rows, so the page states which
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
		# Said once, so the page can head the table with what these rows are.
		"awaiting_supply": awaiting,
		# A submitted plan's blocks are not editable, so the allocation control is not
		"submitted": bool(p.docstatus),
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
			"plants_to_plant": sum(x["plants_to_plant"] for x in new_rows),
			"cuttings": sum(x["cuttings"] for x in new_rows),
			"awaiting_supply": awaiting,
			"blocks_available": blocks_available,
			"blocks_needed": len(new_rows),
			"weeks_in_ground": life_weeks,
			"min_planting_beds": v.min_planting_beds_derived or 0,
		},
	}


def _sticking_demand(plan, version):
	"""Cuttings the field wants each week, from the plan's own sticking schedule."""
	rows = frappe.db.sql("""select sticking_year y, sticking_week w, sum(plants) plants
		from `tabSummer Flower Plan Block`
		where parent = %s and is_new_planting = 1 and ifnull(sticking_year, 0) > 0
		group by sticking_year, sticking_week""", (plan,), as_dict=True)
	out = {}
	for r in rows:
		out[(cint(r.y), cint(r.w))] = int(math.ceil(
			version.cuttings_for_plants(cint(r.plants))))
	return out


@frappe.whitelist()
def simulate_lifecycle(version, tc_qty, order_date, num_cycles=None, to_prop_pct=0,
                       farm_overrides=None, max_bench_sqm=None, plan=None,
                       horizon_weeks=None, build_up_cycles=None):
	"""The weekly TC -> motherstock -> cuttings lifecycle, with propagation feedback."""
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

	# Two different things are called cycles here and they must not be confused.
	if build_up_cycles not in (None, ""):
		v = frappe.get_cached_doc("Crop Protocol Version", version)
		c = cint(build_up_cycles)
		p["multiplication_factor"] = flt(v.multiplication_factor(c))
		p["build_up_cycles"] = c
		# NOT lead_time_for_cycles: that is the time to the full pool, which is one
		p["tc_to_first_cut_weeks"] = cint(v.weeks_tc_to_first_cut())

	# What the field asks for, week by week, keyed to the simulation's own clock so
	demand_by_sw = {}
	if plan:
		v0 = frappe.get_cached_doc("Crop Protocol Version", version)
		base = getdate(order_date)
		for (y, w), cuttings in _sticking_demand(plan, v0).items():
			sw = int((iso_monday(y, w) - base).days // 7)
			if sw >= 0:
				demand_by_sw[sw] = demand_by_sw.get(sw, 0) + cuttings

	res = ls.simulate(
		p, frappe.utils.cint(tc_qty), order_date,
		num_cycles=frappe.utils.cint(num_cycles) or None,
		farm_overrides=farm_overrides,
		default_to_prop_pct=flt(to_prop_pct),
		max_bench_sqm=max_bench_sqm,
		horizon_weeks=cint(horizon_weeks) or None,
		demand_by_sw=demand_by_sw or None,
	)
	# Trim the row payload: the table only needs weeks where something happens.
	res["rows"] = [
		r for r in res["rows"]
		if r["total_cap"] or r["events"] or r["phase"]
	]
	res["build_up_cycles"] = cint(p.get("build_up_cycles"))
	res["multiplication_factor"] = flt(p.get("multiplication_factor"))
	res["tc_to_first_cut_weeks"] = cint(p.get("tc_to_first_cut_weeks"))

	# What the pool is being sized against. The simulator knows what the pool can
	res["peak_cuttings_needed"] = 0
	res["peak_cuttings_week"] = None
	if plan:
		pr = frappe.get_all(
			"Summer Flower Propagation Plan", filters=_prop_filters(plan),
			fields=["peak_weekly_cuttings", "peak_week", "mother_plants_required"],
			order_by="creation desc", limit=1)
		if pr:
			res["peak_cuttings_needed"] = cint(pr[0].peak_weekly_cuttings)
			res["peak_cuttings_week"] = pr[0].peak_week
			res["mother_plants_required"] = cint(pr[0].mother_plants_required)
	return res


def _standing_plants(block, rows, used, basis, register):
	"""How many plants are standing on this block."""
	# What a planting LEAVES standing, not what went into it. plants is the
	from_plantings = sum(cint(r.get("plants_expected_standing")) or cint(r.get("plants"))
	                     for r in rows
	                     if r.get("calendar_status") not in ("Uprooted", "Cancelled"))
	from_beds = sum(x["plants"] for x in used)
	from_register = cint((register.get(block.name) or {}).get("plants"))
	# The largest of the three, not the first that is non-zero. Preferring a
	return max(from_plantings if basis == "plantings" else 0,
	           from_beds, from_register)


@frappe.whitelist()
def block_forecast(plan=None, variety=None, farm=None, blocks=None):
	"""Per-block weekly forecast to the end of each cycle, plus uprooting dates."""
	_guard()
	if isinstance(blocks, str):
		blocks = [b for b in frappe.parse_json(blocks) if b] if blocks.startswith("[") \
			else [blocks]

	bfilters = {"custom_is_summer_flower_block": 1}
	if farm:
		bfilters["farm"] = farm
	all_blocks = frappe.get_all(
		"Block", filters=bfilters,
		fields=["name", "block", "farm", "custom_total_beds", "custom_net_area_ha"],
		order_by="block asc",
	)
	if blocks:
		all_blocks = [b for b in all_blocks if b.name in blocks]

	# What each block says is standing on it, variety by variety. The block keeps
	standing = {}
	if all_blocks:
		gfilters = {"parenttype": "Block", "parent": ["in", [b.name for b in all_blocks]]}
		if variety:
			gfilters["variety"] = variety
		for g in frappe.get_all("Greenhouse Variety", filters=gfilters,
		                        fields=["parent", "variety", "plants", "beds"]):
			k = standing.setdefault(g.parent, {"plants": 0, "beds": 0})
			k["plants"] += cint(g.plants)
			k["beds"] += cint(g.beds)

	pfilters = {"calendar_status": ["not in", ("Cancelled",)]}
	if variety:
		pfilters["variety"] = variety
	plantings = frappe.get_all(
		"Planting Calendar", filters=pfilters,
		fields=["name", "block", "variety", "beds", "plants",
		        "plants_expected_standing", "planting_date",
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
			        "bed_length", "bed_width", "bed_area"],
			order_by="custom_block asc, bed asc",
		):
			beds_by_block.setdefault(r.custom_block, []).append({
				"bed": r.name,
				"number": r.bed,
				"status": r.custom_bed_status or "Empty",
				"planting": r.custom_planting_calendar,
				"plants": r.custom_plants or 0,
				"uproot_date": str(r.custom_uproot_date) if r.custom_uproot_date else None,
				# Dimensions where a bed has them, the recorded area otherwise. The
				"area_sqm": (flt(r.bed_length) * flt(r.bed_width)) or flt(r.bed_area),
			})

	out = []
	for b in all_blocks:
		rows = by_block.get(b.name, [])
		beds = beds_by_block.get(b.name, [])
		# Utilisation is counted off bed records, not off Planting Calendar.beds, so a
		used = [x for x in beds if x["status"] in ("Planted", "Producing")]
		uprooted = [x for x in beds if x["status"] == "Uprooted"]
		free = [x for x in beds if x["status"] not in ("Planted", "Producing", "Uprooted")]
		net_total = sum(x["area_sqm"] for x in beds)
		net_used = sum(x["area_sqm"] for x in used)
		# Most Bed records carry no length/width, so area-based utilisation is not
		measured = len([x for x in beds if x["area_sqm"] > 0])
		# Bed status is the better answer where it is maintained, because a bed pulled
		held = sum(cint(r.get("beds")) for r in rows
		           if r.get("calendar_status") not in ("Uprooted", "Cancelled"))
		basis = "bed status"
		if held and not used:
			basis = "plantings"
			held = min(held, len(beds) or held)
			# Area follows the same basis, or the bar would say 300 beds used and
			per_bed = (net_total / len(beds)) if beds else 0
			net_used = held * per_bed
		entry = {
			"block": b.name,
			"block_code": b.block,
			"farm": b.farm,
			"total_beds": b.custom_total_beds or len(beds),
			"net_area_ha": flt(b.custom_net_area_ha),
			"plantings": [],
			"status": "Not planted",
			"beds": beds,
			"utilisation": {
				"beds_total": len(beds),
				"beds_used": held if basis == "plantings" else len(used),
				"beds_free": (max(0, len(beds) - held) if basis == "plantings"
				              else len(free)),
				"beds_uprooted": len(uprooted),
				# Which of the two answers this is, so a reader can tell a maintained
				"basis": basis,
				"pct_used": round(
					(held if basis == "plantings" else len(used))
					* 100.0 / len(beds), 2) if beds else 0.0,
				"net_sqm_total": round(net_total, 1),
				"net_sqm_used": round(net_used, 1),
				"net_sqm_free": round(net_total - net_used, 1),
				"beds_measured": measured,
				"area_complete": bool(beds) and measured == len(beds),
				"plants_standing": _standing_plants(b, rows, used, basis, standing),
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
			"net_area_ha": round(sum(b["net_area_ha"] for b in out), 3),
			"plants_standing": sum(x["plants_standing"] for x in u),
			"weeks_producing": len([r for r in overlay if r["stems"]]),
			"weeks_in_deficit": len([r for r in overlay if r["variance"] < 0]),
		},
	}


EDITABLE_PROTOCOL_FIELDS = (
	"plants_per_sqm_net", "plants_per_bed", "beds_per_block",
	"min_planting_area_sqm",
	"weeks_to_pinch", "flush_interval_weeks",
	"sticking_to_planting_weeks", "weeks_on_tray",
	"weeks_on_pot", "weeks_to_max_pc", "hardening_weeks", "ramp_weeks",
	"ramp_profile", "supplier_lead_weeks", "weeks_to_max_production",
	"cuttings_per_plant_per_week", "plants_per_pot", "pots_per_sqm",
	"max_multiplication_cycles", "multiplication_factor_per_cycle",
	# Which waits count as establishment. They decide the length of a build-up
	"establishment_includes_hardening", "establishment_includes_ramp",
	"cycle_time_weeks", "motherstock_life_weeks", "rooting_success_pct",
	"field_establishment_pct", "cutting_reject_pct", "stated_yield_stems_per_ha",
	"ready_cutting_price", "climate_note",
)


@frappe.whitelist()
def lifetime_production(variety=None, farm=None, plan=None, grain="month"):
	"""Every stem a standing planting will yield, to the end of its life."""
	_guard()
	if plan and not variety:
		row = frappe.db.get_value("Summer Flower Production Plan", plan,
		                          ["variety", "farm"], as_dict=True)
		if row:
			variety, farm = row.variety, row.farm

	pf = {"calendar_status": ["not in", ("Cancelled", "Uprooted")]}
	if variety:
		pf["variety"] = variety
	if farm:
		pf["farm"] = farm
	plantings = frappe.get_all(
		"Planting Calendar", filters=pf,
		fields=["name", "block", "variety", "farm", "beds", "plants", "planting_date",
		        "planned_uproot_date", "actual_uproot_date", "calendar_status",
		        "crop_protocol_version", "expected_stems_life"],
		order_by="planting_date asc")
	if not plantings:
		return {"variety": variety, "farm": farm, "plantings": [], "rows": [],
		        "totals": {}, "grain": grain}

	# Flush rows carry the dated harvests, and they are the same rows the plan and the
	names = [p.name for p in plantings]
	flushes = frappe.get_all(
		"Planting Calendar Flush",
		filters={"parent": ["in", names]},
		fields=["parent", "flush_number", "harvest_date", "year", "week_no",
		        "expected_stems", "actual_stems", "is_harvested", "stems_per_plant"],
		order_by="harvest_date asc")

	today = getdate(nowdate())
	buckets = {}
	by_planting = {}
	for f in flushes:
		if not f.harvest_date:
			continue
		d = getdate(f.harvest_date)
		key = ("%s-%02d" % (d.year, d.month) if grain == "month"
		       else "%s-W%02d" % (cint(f.year), cint(f.week_no)))
		b = buckets.setdefault(key, {"period": key, "date": str(d),
		                             "expected": 0, "actual": 0, "harvested": 0,
		                             "future": 0, "plantings": set()})
		exp, act = cint(f.expected_stems), cint(f.actual_stems)
		b["expected"] += exp
		b["actual"] += act
		b["plantings"].add(f.parent)
		if f.is_harvested:
			b["harvested"] += act
		elif d >= today:
			b["future"] += exp
		p = by_planting.setdefault(f.parent, {"expected": 0, "harvested": 0,
		                                      "remaining": 0, "flushes": 0,
		                                      "last": None})
		p["expected"] += exp
		p["flushes"] += 1
		p["last"] = str(d)
		if f.is_harvested:
			p["harvested"] += act
		elif d >= today:
			p["remaining"] += exp

	rows = [{**b, "plantings": len(b["plantings"])}
	        for b in sorted(buckets.values(), key=lambda x: x["date"])]
	run = 0
	for r in rows:
		run += r["expected"]
		r["cumulative"] = run

	out_plantings = []
	for p in plantings:
		agg = by_planting.get(p.name, {})
		out_plantings.append({
			"planting": p.name, "block": p.block, "beds": cint(p.beds),
			"plants": cint(p.plants), "status": p.calendar_status,
			"planted": str(p.planting_date) if p.planting_date else None,
			"uproot": str(p.actual_uproot_date or p.planned_uproot_date or ""),
			"protocol": p.crop_protocol_version,
			"flushes": agg.get("flushes", 0),
			"stems_expected": agg.get("expected", 0),
			"stems_harvested": agg.get("harvested", 0),
			"stems_remaining": agg.get("remaining", 0),
			"last_harvest": agg.get("last"),
			# What the planting itself claims, so a flush table that has drifted from
			"stems_on_record": cint(p.expected_stems_life),
		})
	return {
		"variety": variety, "farm": farm, "grain": grain,
		"plantings": out_plantings, "rows": rows,
		"totals": {
			"plantings": len(out_plantings),
			"beds": sum(x["beds"] for x in out_plantings),
			"plants": sum(x["plants"] for x in out_plantings),
			"stems_expected": sum(x["stems_expected"] for x in out_plantings),
			"stems_harvested": sum(x["stems_harvested"] for x in out_plantings),
			"stems_remaining": sum(x["stems_remaining"] for x in out_plantings),
			"first_harvest": rows[0]["date"] if rows else None,
			"last_harvest": rows[-1]["date"] if rows else None,
			"periods": len(rows),
		},
	}


# Stages that mean the farm cuts its own material off a standing pool. A route
from upande_summer_flowers.summer_flowers import stages as _stages

MOTHERSTOCK_STAGES = _stages.StageSet((_stages.MOTHERSTOCK, _stages.TAKEN))

# Which lift profile belongs to which way in.
class _ChainForm:
	def get(self, stage, default=None):
		return _stages.lift_forms().get(stage, default)

	def __getitem__(self, stage):
		return _stages.lift_forms()[stage]

	def __contains__(self, stage):
		return stage in _stages.lift_forms()


CHAIN_FORM = _ChainForm()


def _stated_lifts(version, form):
	"""The lift rows for `form`, from the version or the protocol behind it."""
	rows = [r for r in (version.get("lifts") or [])
	        if (r.get("form") or "") == form] if hasattr(version, "get") else []
	if not rows and hasattr(version, "get") and version.get("crop_protocol"):
		try:
			doc = frappe.get_cached_doc("Crop Protocol",
			                            version.get("crop_protocol"))
			rows = [r for r in (doc.get("custom_sf_lifts") or [])
			        if (r.get("form") or "") == form]
		except Exception:
			frappe.clear_last_message()
			rows = []
	return {cint(r.get("lift_no")): r for r in rows}


def _route_chains_payload(version):
	"""Each way in, as its own route, with what it costs in time."""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	out = []
	for chain in (cp.route_chains(version) or []):
		stages = []
		for stage, steps in chain:
			stages.append({
				"stage": stage.get("stage"),
				"weeks": cint(stage.get("weeks")),
				"loss_pct": flt(stage.get("loss_pct")),
				"is_purchase": cint(stage.get("is_purchase")),
				"lead_weeks": cint(stage.get("lead_weeks")),
				"item": stage.get("item"),
				"returns_per_plant": flt(stage.get("returns_per_plant")),
				"share_pct": flt(stage.get("share_pct")),
				"steps": [{"step": x.get("step"), "weeks": cint(x.get("weeks")),
				           "loss_pct": flt(x.get("loss_pct"))} for x in steps],
			})
		if not stages:
			continue
		entry = stages[0]
		# The entry stage is the material; the weeks that matter are what
		after = sum(x["weeks"] for x in stages[1:]) + sum(
			y["weeks"] for x in stages[1:] for y in x["steps"])
		# How this material is handed over once it is in. A consignment is not
		form = CHAIN_FORM.get(entry["stage"])
		lifts = []
		if form:
			from upande_summer_flowers.summer_flowers import tc_supply as ts

			# The two halves as well as the total, because the week a lift
			stated = _stated_lifts(version, form)
			for i, (wk, share) in enumerate(
					ts.handover_waves(form, None, after, version=version), 1):
				row = stated.get(i) or {}
				lifts.append({"lift": i, "weeks_after_arrival": cint(wk),
				              "trough_weeks": cint(row.get("trough_weeks")),
				              "rooting_weeks": cint(row.get("rooting_weeks")),
				              "share_pct": round(share * 100, 1)})
		out.append({
			"entry": entry["stage"],
			"form": form,
			"lifts": lifts,
			"label": " → ".join(x["stage"] for x in stages if x["stage"]),
			"is_purchase": entry["is_purchase"],
			"lead_weeks": entry["lead_weeks"],
			"weeks_to_ground": after,
			"notice_weeks": entry["lead_weeks"] + after,
			"returns_per_plant": entry["returns_per_plant"],
			"item": entry["item"],
			"share_pct": entry["share_pct"],
			"stages": stages,
		})
	return out


def _end_stage():
	from upande_summer_flowers.summer_flowers import stages as _stages

	return _stages.end_stage()


def _pool_journey(v, lead, estab, cycles, build, to_cut, stick_to_plant,
                  plant_to_harvest):
	"""Tissue culture to a motherstock to a cutting in the ground."""
	journey = [
		{"week": 0, "label": "Place TC order", "note": None},
		{"week": lead, "label": "TC arrives", "note": f"{lead}w supplier lead"},
		{"week": lead + cint(v.weeks_on_tray), "label": "Tray to pot",
		 "note": f"{cint(v.weeks_on_tray)}w tray"},
		{"week": lead + cint(v.weeks_on_tray) + cint(v.weeks_on_pot),
		 "label": "Pot ends, ramp begins", "note": f"{cint(v.weeks_on_pot)}w pot"},
		{"week": lead + estab, "label": "First generation ready",
		 "note": f"{estab}w establishment, ramp {v.ramp_profile or ''}"},
	]
	if cycles:
		journey += [
			{"week": lead + estab + build, "label": "Multiplication done",
			 "note": f"{cycles} cycles x {cint(v.cycle_time_weeks)}w"},
			{"week": lead + to_cut, "label": "Motherstock cutting",
			 "note": f"{estab}w to establish the multiplied generation"},
		]
	else:
		journey.append({"week": lead + to_cut, "label": "Motherstock cutting",
		                "note": "no build-up"})
	journey += [
		{"week": lead + to_cut + stick_to_plant, "label": "Cuttings planted",
		 "note": f"{stick_to_plant}w sticking to planting"},
		{"week": lead + to_cut + stick_to_plant + cint(v.weeks_to_pinch),
		 "label": "Pinch", "note": f"{cint(v.weeks_to_pinch)}w to pinch"},
		{"week": lead + to_cut + stick_to_plant + plant_to_harvest,
		 "label": "First harvest",
		 "note": f"{plant_to_harvest}w planting to first harvest"},
	]
	return journey, _("Full TC journey — order to first farm harvest")


def _entry_lifts(v, stage):
	"""The lift profile stated for the way in, or nothing."""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp
	from upande_summer_flowers.summer_flowers.route_ways import LIFT_FORM

	form = LIFT_FORM.get(stage)
	if not form:
		return []
	try:
		return cp.protocol_lifts(v, form) or []
	except Exception:
		frappe.clear_last_message()
		return []


def _route_journey(v, plant_to_harvest):
	"""Order to first harvest, read off the route the crop actually takes."""
	rows = [r for r in (v.get("material_route") or [])
	        if (r.get("row_type") or "Stage") == "Stage"]
	entry = next((r for r in rows if cint(r.get("is_purchase"))), None)
	start = rows.index(entry) if entry else 0
	# One way in, not the whole table. A crop bought two ways has its chains one
	chain = []
	for r in rows[start:]:
		chain.append(r)
		if r.stage == _end_stage():
			break
	lead = cint(entry.lead_weeks) if entry else 0
	name = entry.stage if entry else (chain[0].stage if chain else _("Material"))

	week = 0
	journey = []
	if entry:
		journey.append({"week": 0,
		                "label": _("Place {0} order").format(name), "note": None})
		week = lead
		journey.append({
			"week": week,
			# "Seeds arrives" is nobody's English. The stage names are plural as
			"label": (_("{0} arrive") if name.endswith("s") else _("{0} arrives"))
			.format(name),
			"note": _("{0}w supplier lead").format(lead)})
	# How the consignment is handed over, where the protocol says. A profile
	# of "90% at seven weeks, the rest at eight" is two plantings, not one,
	# and the journey said neither -- it said the stage's flat weeks.
	waves = _entry_lifts(v, name)
	for r in chain[1:]:
		weeks = cint(r.weeks)
		end = r.stage == _end_stage()
		if end and len(waves) > 1:
			for i, (after, share) in enumerate(waves):
				journey.append({
					"week": lead + cint(after),
					"label": _("Planted — lift {0}").format(i + 1),
					"note": _("{0}% of the consignment, {1}w after it arrives"
					          ).format(round(share * 100, 1), cint(after)),
				})
			week = lead + cint(waves[0][0])
			continue
		week += weeks
		journey.append({
			"week": week,
			"label": _("Planted") if end else r.stage,
			# The note is the gap BEFORE this step, the way the pool journey
			"note": (_("{0}w {1}").format(weeks, r.stage.lower())
			         if weeks and not end else None),
		})
	journey.append({"week": week + plant_to_harvest, "label": _("First harvest"),
	                "note": _("{0}w planting to first harvest").format(plant_to_harvest)})
	# In the order it happens. A first lift at seven weeks and a propagation
	# stage at eight put "Propagation" after the planting it precedes.
	journey.sort(key=lambda j: cint(j["week"]))
	return journey, _("Full {0} journey — order to first farm harvest").format(name)


@frappe.whitelist()
def protocol_detail(version=None, variety=None, farm=None, plan=None):
	"""The whole protocol sheet: inputs, derived values and the journey timelines."""
	_guard()
	source = "picked"
	if not version and plan:
		row = frappe.db.get_value("Summer Flower Production Plan", plan,
		                          ["protocol", "variety", "farm"], as_dict=True)
		if row and row.protocol:
			version, variety, farm = row.protocol, row.variety, row.farm
			source = "plan"
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
		source = "in force"

	v = frappe.get_doc("Crop Protocol Version", version)

	flushes = []
	cum = 0
	# Per hectare of BED. It is the only area this app carries.
	plants_per_ha = (v.plants_per_sqm_net or 0) * bed_sqm_per_ha()
	for r in sorted(v.flush_schedule, key=lambda r: r.flush_number or 0):
		cum += r.stems_per_plant or 0
		flushes.append({
			"flush": r.flush_number,
			"weeks_from_pinch": r.weeks_from_pinch,
			"weeks_from_planting": (v.weeks_to_pinch or 0) + (r.weeks_from_pinch or 0),
			# Kept for callers that still read it; there is no grid allowance now, so
			"weeks_from_planting_gridded": (v.weeks_to_pinch or 0)
			+ (r.weeks_from_pinch or 0),
			"stems_per_plant": flt(r.stems_per_plant),
			"stems_per_ha": int(round(flt(r.stems_per_plant) * plants_per_ha)),
			"cumulative_stems_per_plant": round(cum, 2),
			# The share of the plant's life this flush is, and everything gathered by
			"pct_of_life": flt(r.pct_of_life),
			"cumulative_pct": flt(r.cumulative_pct),
		})

	# How the material gets to a plant, so the page can say what this crop is raised
	route = []
	for r in (v.material_route or []):
		route.append({
			"row_type": r.row_type or "Stage",
			"stage": r.stage, "step": r.step,
			"weeks": cint(r.weeks), "loss_pct": flt(r.loss_pct),
			"yields_per_unit": flt(r.yields_per_unit or 1),
			"is_purchase": cint(r.is_purchase),
			"lead_weeks": cint(r.lead_weeks), "rate": flt(r.rate),
			"item": r.item,
			# The split the planner reads off this protocol. It was computed
			"share_pct": flt(r.share_pct),
			"returns_per_plant": flt(r.returns_per_plant),
		})
	bought = [r["stage"] for r in route if r["is_purchase"] and r["stage"]]

	# The route as the chains it actually is, rather than one flat list. A
	chains = _route_chains_payload(v)
	# Whether there is a motherstock at all. A crop handed its material has no
	has_ms = bool([r for r in route
	               if (r["stage"] or "") in MOTHERSTOCK_STAGES])

	# TC order through to the first harvest off those cuttings.
	lead = cint(v.supplier_lead_weeks)
	estab = cint(v.ms_establishment_weeks)
	cycles = cint(v.max_multiplication_cycles)
	build = cycles * cint(v.cycle_time_weeks)
	to_cut = cint(v.lead_time_weeks)          # arrival to the first cutting
	stick_to_plant = cint(v.sticking_to_planting_weeks)
	plant_to_harvest = cint(v.first_harvest_offset_weeks)

	# A crop with no pool does not take the tissue-culture road, and telling it
	if not _has_pool(v):
		journey, journey_title = _route_journey(v, plant_to_harvest)
	else:
		journey, journey_title = _pool_journey(
			v, lead, estab, cycles, build, to_cut, stick_to_plant, plant_to_harvest)

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
		# Which of the three routes chose this version, so the tab can say whether it
		"source": source,
		"for_plan": plan if source == "plan" else None,
		"change_reason": v.change_reason,
		"editable": v.version_status == "Draft",
		"climate_note": v.climate_note,
		"fields": {k: v.get(k) for k in EDITABLE_PROTOCOL_FIELDS},
		"derived": {
			"sqm_net_per_bed": flt(v.sqm_net_per_bed),
			"plants_per_ha": plants_per_ha,
			"plants_per_block": v.plants_per_block or 0,
			"min_planting_plants": v.min_planting_plants or 0,
			"min_planting_beds": v.min_planting_beds_derived or 0,
			"min_planting_area_sqm": flt(v.min_planting_area_sqm),
			"total_flushes": v.total_flushes or 0,
			"total_stems_per_plant_life": flt(v.total_stems_per_plant_life),
			# Worked out here rather than read off the snapshot, so they agree with
			"stems_per_ha_life": flt(v.total_stems_per_plant_life) * plants_per_ha,
			"stems_per_ha_year": ((flt(v.total_stems_per_plant_life) * plants_per_ha)
			                      / flt(v.life_expectancy_years)
			                      if flt(v.life_expectancy_years) else 0),
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
		"route": route,
		"route_summary": " → ".join(r["stage"] for r in route
		                            if r["row_type"] != "Step" and r["stage"]),
		"chains": chains,
		"has_motherstock": 1 if has_ms else 0,
		"bought_at": bought,
		"grades": [{"grade": g.grade, "pct": flt(g.allocation_pct),
		            "price": _stem_price(v.variety)} for g in v.grade_allocation],
		"ramp": ramp,
		"journey": journey,
		"journey_title": journey_title,
		# What this crop is bought as, so the card can name it instead of
		"entry_stage": next((r.stage for r in (v.get("material_route") or [])
		                     if cint(r.get("is_purchase"))), None),
		"versions": sibling,
	}


@frappe.whitelist()
def save_protocol(version, changes, flushes=None, grades=None, change_reason=None,
                  effective_from=None):
	"""Apply protocol edits, honouring the versioning rules."""
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

	# Silently dropping an edit is worse than refusing it: the save reports success,
	refused = [k for k in (changes or {}) if k not in EDITABLE_PROTOCOL_FIELDS]
	if refused:
		frappe.throw(
			_("These are not editable on a protocol version: {0}.").format(
				", ".join(sorted(refused))),
			title=_("Cannot change"))
	for k, val in (changes or {}).items():
		v.set(k, val)

	if flushes is not None:
		v.flush_schedule = []
		for i, f in enumerate(flushes, start=1):
			v.append("flush_schedule", {
				"flush_number": i,
				"weeks_from_planting": frappe.utils.cint(
					f.get("weeks_from_planting") or f.get("weeks_from_pinch")),
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
		    f"Order {(b.tc_plants_required or 0):,} TC plants", b.name, None,
		    b.tc_plants_required)
		add(b.tc_on_farm_date, "tc_arrive", "TC plants on farm", b.name)
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
		                      filters=_prop_filters(plan), pluck="name",
		                      order_by="creation desc", limit=1)
		if not rows:
			return {"propagation_plan": None, "plan": plan,
			        "reason": "not created yet"}
		propagation_plan = rows[0]

	d = frappe.get_doc("Summer Flower Propagation Plan", propagation_plan)
	# What one mother plant yields in a week. Everything below converts cuttings to
	per_week = flt(frappe.db.get_value(
		"Crop Protocol Version",
		frappe.db.get_value("Summer Flower Production Plan", d.production_plan, "protocol"),
		"cuttings_per_plant_per_week")) or 1.0
	batches = frappe.get_all(
		"Summer Flower Motherstock Batch",
		filters={"production_plan": d.production_plan},
		fields=["name", "batch_status", "mother_plants", "tc_plants_required",
		        "tc_order_date", "tc_on_farm_date", "first_sticking_date",
		        "expiry_date", "total_cost", "currency", "bench_sqm"])
	# The protocol is the production plan's, and so is the question of whether it
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
		# Mother plants, not cuttings. A week's cuttings are what the field wants;
		"weeks": [{
			"year": r.year, "week_no": r.week_no,
			"label": "%s-W%02d" % (r.year, cint(r.week_no)),
			"week_start_date": str(r.week_start_date or ""),
			"plants_to_stick": cint(r.plants_to_stick),
			"cuttings_required": cint(r.cuttings_required),
			"mothers_required": (int(math.ceil(cint(r.cuttings_required) / per_week))
			                     if per_week else 0),
			"mothers_available": (int(cint(r.capacity_available) / per_week)
			                      if per_week else 0),
			"from_existing_ms": cint(r.from_existing_ms),
			"from_new_ms": cint(r.from_new_ms),
			"shortfall": cint(r.shortfall),
			"capacity": cint(r.capacity_available),
			"ramp_pct": flt(r.ramp_pct),
			"plant_week": r.plant_week,
			"deliver_on": str(r.deliver_on or ""),
			"promised": cint(r.baseline_plants),
			"delivered": cint(r.actual_plants),
			"delivered_on": str(r.actual_on or ""),
			"variance": cint(r.variance_plants),
			"state": r.state,
			"uplift": cint(r.uplift_plants),
			"recorded_from": r.recorded_from,
		} for r in d.weeks],
		# What the unit was sent and what it did with it. Separate from the
		"consignments": [{
			"week": str(r.week_start_date or ""), "week_iso": r.week_iso,
			"form": r.form, "source": r.source, "reference": r.reference,
			"units": cint(r.units), "plants": cint(r.plants),
			"ready_on": str(r.ready_on or ""),
			"deliver_on": str(r.deliver_on or ""),
			"actual_units": cint(r.actual_units),
			"variance_units": cint(r.variance_units),
			"actual_on": str(r.actual_on or ""),
			"days_late": cint(r.days_late),
			"state": r.state, "recorded_from": r.recorded_from,
		} for r in d.intake],
		"actuals": {
			"recorded": cint(d.actuals_recorded),
			"reported_to": str(d.reported_to or ""),
			"weeks_closed": cint(d.weeks_closed),
			"tc_received": cint(d.tc_received),
			"roots_withdrawn": cint(d.roots_withdrawn),
			"plants_to_farm": cint(d.plants_to_farm),
			"plants_lost": cint(d.plants_lost),
			"short": cint(d.delivered_short),
			"ahead": cint(d.delivered_ahead),
			"carried_short": cint(d.carried_short),
			"survival_pct": flt(d.actual_survival_pct),
			"survival_basis": d.survival_basis,
			"note": d.actuals_note,
			"baseline_on": str(d.baseline_taken_on or ""),
			"replan_cover": cint(d.replan_cover),
			"replan_still_short": cint(d.replan_still_short),
			"replan_note": d.replan_note,
		},
		"sources": [{
			"source_type": r.source_type, "batch": r.motherstock_batch,
			"mother_plants": cint(r.mother_plants),
			"weekly_capacity": cint(r.weekly_capacity),
			"available_from": str(r.available_from or ""),
			"available_to": str(r.available_to or ""),
			"notes": r.notes,
		} for r in d.sources],
		"batches": batches,
		# Who else is raising this season. The allocation lives on the
		"procurement": d.procurement_summary(),
		"allocations": d.allocation_summary(),
	}


@frappe.whitelist()
def plan_family(name):
	"""A production plan and every revision of it, in both directions."""
	if not name:
		return []
	seen, cur = {name}, name
	while True:
		up = frappe.db.get_value("Summer Flower Production Plan", cur, "amended_from")
		if not up or up in seen:
			break
		seen.add(up)
		cur = up
	frontier = list(seen)
	while frontier:
		kids = [k for k in frappe.get_all(
			"Summer Flower Production Plan",
			filters={"amended_from": ["in", frontier]}, pluck="name") if k not in seen]
		seen.update(kids)
		frontier = kids
	return sorted(seen)


def superseded_by(plan):
	"""The live revision standing in for a cancelled plan, where there is one."""
	family = [n for n in plan_family(plan) if n != plan]
	live = frappe.get_all("Summer Flower Production Plan",
	                      filters={"name": ["in", family], "docstatus": ["<", 2]},
	                      fields=["name", "status", "docstatus"],
	                      order_by="creation desc", limit=1) if family else []
	return live[0] if live else None


@frappe.whitelist()
def procurement_detail(plan=None, variety=None, farm=None):
	"""What was bought for a production plan, and who was allocated to raise it."""
	_guard()
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None, "plans": [], "allocations": {"rows": []},
		        "reason": "no production plan in scope"}
	plan_state = frappe.db.get_value("Summer Flower Production Plan", plan,
	                                 ["docstatus", "status"], as_dict=True) or {}
	revision = superseded_by(plan) if cint(plan_state.get("docstatus")) == 2 else None

	# A revision is the same plan under a new name: approving a schedule cancels
	# the production plan and repoints its procurement plan at the revision, and
	# a screen still showing the old name should say so rather than go blank.
	family = plan_family(plan)
	plans = frappe.get_all(
		"Summer Flower Procurement Plan",
		filters={"production_plan": ["in", family], "docstatus": ["<", 2]},
		fields=["name", "status", "docstatus", "method", "entry_stage", "supplier",
		        "total_units_to_order", "total_plants_at_field", "plants_per_unit",
		        "first_order_by", "last_order_by", "orders_late", "propagates_here",
		        "beds_required", "beds_available", "capped_by_space", "space_note",
		        "lead_weeks", "weeks_to_ground", "sizing_basis", "route_summary",
		        "pool_plants", "weekly_draw", "cycles_suggested",
		        "production_plan"],
		order_by="creation desc")
	# Nothing live in the family: a cancelled plan is still what was bought, and
	# hiding it tells the buyer their work was lost.
	cancelled = []
	if not plans:
		cancelled = frappe.get_all(
			"Summer Flower Procurement Plan",
			filters={"production_plan": ["in", family], "docstatus": 2},
			fields=["name", "status", "docstatus", "method", "entry_stage", "supplier",
			        "total_units_to_order", "total_plants_at_field", "plants_per_unit",
			        "first_order_by", "last_order_by", "orders_late", "propagates_here",
			        "beds_required", "beds_available", "capped_by_space", "space_note",
			        "lead_weeks", "weeks_to_ground", "sizing_basis", "route_summary",
			        "pool_plants", "weekly_draw", "cycles_suggested",
			        "production_plan"],
			order_by="creation desc")
		plans = cancelled
	# Still nothing: the buying may sit on another plan for the same variety,
	# farm and season -- a second plan built beside this one. It is the same
	# material being bought, so it is shown, named for the plan it belongs to.
	elsewhere = []
	if not plans:
		scope = frappe.db.get_value("Summer Flower Production Plan", plan,
		                            ["variety", "farm", "season"], as_dict=True) or {}
		if scope.get("variety") and scope.get("farm"):
			elsewhere = frappe.get_all(
				"Summer Flower Procurement Plan",
				filters={"variety": scope["variety"], "farm": scope["farm"],
				         "season": scope.get("season"), "docstatus": ["<", 2]},
				fields=["name", "status", "docstatus", "method", "entry_stage", "supplier",
			        "total_units_to_order", "total_plants_at_field", "plants_per_unit",
			        "first_order_by", "last_order_by", "orders_late", "propagates_here",
			        "beds_required", "beds_available", "capped_by_space", "space_note",
			        "lead_weeks", "weeks_to_ground", "sizing_basis", "route_summary",
			        "pool_plants", "weekly_draw", "cycles_suggested",
			        "production_plan"],
				order_by="creation desc")
		seen_stage = set()
		for pr in elsewhere:
			key = pr.get("entry_stage") or ""
			if key in seen_stage:
				continue
			seen_stage.add(key)
			pr["foreign"] = 1
			plans.append(pr)

	for pr in plans:
		pr["production_plan"] = pr.get("production_plan") or plan
		pr["for_another_revision"] = 0 if pr["production_plan"] == plan else 1
		pr["cancelled"] = 1 if cint(pr.get("docstatus")) == 2 else 0

	rows = []
	for pr in plans:
		for r in frappe.get_all(
				"Summer Flower Propagation Allocation",
				filters={"parent": pr["name"],
				         "parenttype": "Summer Flower Procurement Plan"},
				fields=["propagation_unit", "supplier", "plants", "share_pct", "raises",
				        "planted", "surplus", "troughs_used", "plantings",
				        "propagation_plan", "note", "rate_per_plant", "amount",
				        "purchase_order",
				        # What this row raises and when the farm gets it. Both are
				        "alloc_type", "lead_weeks", "start_by",
				        "plants_from", "plants_to", "ready_basis"],
				order_by="idx"):
			r["procurement_plan"] = pr["name"]
			r["entry_stage"] = pr["entry_stage"]
			r["editable"] = (1 if pr["docstatus"] == 0 and not pr.get("foreign")
			                 else 0)
			rows.append(r)

	# Who SELLS it, beside who raises it. The dashboard could allocate the
	buying = []
	for pr in plans:
		for r in frappe.get_all(
				"Summer Flower Purchase Allocation",
				filters={"parent": pr["name"],
				         "parenttype": "Summer Flower Procurement Plan"},
				fields=["entry_stage", "supplier", "share_pct", "units", "rate",
				        "amount", "lead_weeks", "order_by", "arrives",
				        "material_request", "purchase_order", "note"],
				order_by="idx"):
			r["procurement_plan"] = pr["name"]
			r["editable"] = (1 if pr["docstatus"] == 0 and not pr.get("foreign")
			                 else 0)
			buying.append(r)

	season = 0
	try:
		from upande_propagation.allocation import plan_weeks
		season = int(sum(q for _d, q in plan_weeks(plan)))
	except Exception:
		season = cint(frappe.db.get_value("Summer Flower Production Plan",
		                                  plan, "total_plants"))
	allocated = sum(cint(r.get("plants")) for r in rows)
	return {
		"plan_docstatus": cint(plan_state.get("docstatus")),
		"plan_status": plan_state.get("status"),
		# Where the plan went when a confirmed schedule revised it. The screen
		# keeps the name the user picked and offers the revision rather than
		# saying there is nothing here.
		"revised_to": (revision or {}).get("name"),
		"family": [n for n in family if n != plan],
		"plan": plan,
		"plans": plans,
		"buying": buying,
		"allocations": {
			"rows": rows,
			"season_plants": season,
			"allocated": allocated,
			"to_buy": max(season - allocated, 0),
			"units": len({r["propagation_unit"] for r in rows
			              if r.get("propagation_unit")}),
		},
	}


@frappe.whitelist()
def confirm_tc_choice(plan, tc_qty=None, order_date=None, cycles=None, reason=None):
	"""Commit a tried quantity, order date or build-up, and carry it downstream."""
	_guard()
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	if p.docstatus == 1:
		frappe.throw(_("{0} is approved. Amend it to change the TC order.").format(plan))
	if p.docstatus == 2:
		frappe.throw(_("{0} is cancelled.").format(plan))

	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	cycles = cint(cycles) if cycles not in (None, "") else cint(v.max_multiplication_cycles)
	tc_qty = cint(tc_qty) or cint(p.tc_plants_to_order)

	# Cycles and the order date are one decision, not two. Fewer build-up cycles is
	was_cycles = (cint(p.tc_cycles_committed) if p.tc_choice_committed
	              else cint(v.max_multiplication_cycles))
	if order_date:
		order_date = getdate(order_date)
	elif cycles != was_cycles:
		order_date = tc_order_by_date(v, _first_sticking_for(plan), cycles=cycles)
	else:
		order_date = getdate(p.tc_order_by_date) if p.tc_order_by_date else None
	if not tc_qty:
		frappe.throw(_("Nothing to confirm: no TC quantity."))

	before = {
		"tc_qty": cint(p.tc_plants_to_order),
		"order_date": str(p.tc_order_by_date or ""),
		"cycles": cint(p.tc_cycles_committed) or cint(v.max_multiplication_cycles),
		"production_stems": cint(p.total_production_stems),
		"coverage_pct": flt(p.coverage_pct),
	}

	p.tc_choice_committed = 1
	p.tc_plants_committed = tc_qty
	p.tc_order_date_committed = order_date
	p.tc_cycles_committed = cycles
	p.save(ignore_permissions=True)

	changed = []
	# ---- the propagation plan, where one exists for this variety and season
	prop_name = None
	# The one raised for THIS plan first. _prop_filters matches on variety and
	prop = frappe.get_all("Summer Flower Propagation Plan",
	                      filters={"production_plan": plan, "docstatus": ["<", 2]},
	                      fields=["name", "docstatus"], order_by="creation desc", limit=1)
	# and no fallback. Matching on variety and season would find the propagation
	if prop and cint(prop[0].docstatus) == 0:
		prop_name = prop[0].name
		pd = frappe.get_doc("Summer Flower Propagation Plan", prop_name)
		if cint(pd.tc_plants_required) != tc_qty or (
				order_date and getdate(pd.tc_order_date or order_date) != order_date):
			pd.tc_plants_required = tc_qty
			if order_date:
				pd.tc_order_date = order_date
			pd.save(ignore_permissions=True)
			changed.append(_("propagation plan {0}").format(prop_name))
	elif prop:
		changed.append(_("propagation plan {0} is submitted and was left alone")
		               .format(prop[0].name))
	else:
		changed.append(_("no propagation plan yet -- the procurement plan raises one "
		                 "when it is approved, and this choice goes with it"))

	# ---- the procurement plan, which is what actually places the order
	for q in frappe.get_all("Summer Flower Procurement Plan",
	                        filters={"production_plan": plan, "docstatus": ["<", 2]},
	                        fields=["name", "docstatus"], order_by="creation desc"):
		if cint(q.docstatus) == 1:
			changed.append(_("procurement plan {0} is approved and was left alone; "
			                 "amend it to re-order").format(q.name))
			continue
		qd = frappe.get_doc("Summer Flower Procurement Plan", q.name)
		est = [r for r in qd.requirements if (r.line_type or "") == "Establishment"]
		if not est:
			changed.append(_("procurement plan {0} has no establishment line to "
			                 "carry it to").format(q.name))
			continue
		row = est[0]
		row.qty_to_order = tc_qty
		if order_date:
			row.order_by_date = order_date
		row.qty_at_field = int(round(v.mother_plants_for(tc_qty, cycles)))
		qd.pool_plants = row.qty_at_field
		qd.sizing_basis = _(
			"Confirmed on the dashboard: {0} TC plants at {1} multiplication cycles, "
			"ordering {2}. This is a decision, not the calculation -- what the "
			"protocol works out is on the production plan."
		).format(f"{tc_qty:,}", cycles, order_date or _("(no date)"))
		qd.flags.ignore_permissions = True
		qd.save()
		changed.append(_("procurement plan {0}").format(q.name))

	# ---- the motherstock batches raised for this plan
	for b in frappe.get_all("Summer Flower Motherstock Batch",
	                        filters={"production_plan": plan, "docstatus": 0},
	                        fields=["name"]):
		bd = frappe.get_doc("Summer Flower Motherstock Batch", b.name)
		bd.build_up_cycles = cycles
		# Confirming what the maths already says should clear the override, not pin
		bd.override_tc_plants = 1 if tc_qty != cint(bd.tc_plants_calculated) else 0
		bd.tc_plants_required = tc_qty
		if order_date:
			bd.override_tc_order_date = 1 if getdate(
				bd.tc_order_date_calculated or order_date) != order_date else 0
			bd.tc_order_date = order_date
		bd.save(ignore_permissions=True)
		changed.append(_("batch {0}").format(b.name))

	p.reload()
	after = {
		"tc_qty": cint(p.tc_plants_to_order),
		"order_date": str(p.tc_order_by_date or ""),
		"cycles": cint(p.tc_cycles_committed),
		"production_stems": cint(p.total_production_stems),
		"supplied_stems": cint(p.supplied_production_stems),
		"coverage_pct": flt(p.coverage_pct),
		"supplied_coverage_pct": flt(p.supplied_coverage_pct),
	}

	note = _("Confirmed {0} TC plants, {1} cycles, ordering {2}. That order supplies "
	         "{3} stems, {4}% of the demand.").format(
		after["tc_qty"], after["cycles"], after["order_date"] or _("(no date)"),
		after["supplied_stems"], round(after["supplied_coverage_pct"], 1))
	if reason:
		note += "\n\n" + _("Reason given: {0}").format(reason)
	if changed:
		note += "\n\n" + _("Carried through to {0}.").format(", ".join(changed))
	p.db_set("tc_choice_note", note, update_modified=False)

	return {"plan": plan, "before": before, "after": after,
	        "propagation_plan": prop_name, "changed": changed, "note": note}


@frappe.whitelist()
def production_sheet(plan=None, variety=None, farm=None):
	"""The plan as one row per planting and one column per week."""
	_guard()
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None}
	from upande_summer_flowers.summer_flowers import plan_sheet

	return plan_sheet.build(plan)


# The CSV is plan_sheet.sheet_csv, which is whitelisted and writes the download


@frappe.whitelist()
def motherstock_line(plan=None, variety=None, farm=None, tc=None,
                     multiplications=None, divert_weeks=None):
	"""The motherstock line for the dashboard: the decision, and what it gives."""
	_guard()
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	name = resolve_plan(variety=variety, farm=farm, plan=plan)
	if not name:
		return {"plan": None}
	# The agreed line, if somebody has committed to one. Read BEFORE solving, not
	saved = frappe.db.get_value(
		"Summer Flower Motherstock Plan", {"production_plan": name},
		["name", "tc_to_order", "multiplications", "order_by_date", "generations"],
		as_dict=True)
	# The agreed line is a PAIR -- so many plantlets at so many multiplications --
	if saved and multiplications in (None, "") and not cint(tc):
		multiplications = cint(saved.multiplications)
		tc = cint(saved.tc_to_order)

	# A quantity on its own is a question, not an override: "I can afford this
	derived = None
	if cint(tc) and multiplications in (None, ""):
		plan_doc = frappe.get_doc("Summer Flower Production Plan", name)
		derived = ps.multiplications_for_tc(plan_doc, cint(tc))
		if derived:
			multiplications = cint(derived["needed"])

	# What was ASKED for, before the protocol's cap is applied to it. The card
	asked = cint(multiplications) if multiplications not in (None, "") else None

	out = ps.peak_options(name, cycles=multiplications, tc=tc)
	out["plan"] = name
	if derived:
		out["from_quantity"] = {
			"tc": derived["tc"],
			"needed": derived["needed"],
			"beyond_protocol": derived["beyond_protocol"],
			"max_usual": derived["max_usual"],
			"covers": derived["covers"],
			"to_propagation": derived["to_propagation"],
		}
	out["scope"] = scope_of(variety=variety, farm=farm, plan=plan)

	sizing, sch = out.get("sizing"), out.get("schedule")
	if sizing and sch:
		# Flattened into the shape the card draws, so the dashboard and the
		out["chosen"] = {
			"tc": sch["tc"],
			"multiplications": sch["cycles"],
			"multiplications_asked": asked,
			"capped": bool(asked is not None and asked > cint(sch["cycles"])),
			"sends_made": sch.get("sends_made"),
			"sends_allowed": sch.get("sends_allowed"),
			"order_by": sch["order_by"],
			"first_cut_date": sch["first_cut_date"],
			"line_end_date": sch["line_end_date"],
			"peak_pool": sch["peak_pool"],
			"mothers_removed": sch.get("mothers_removed"),
			"cuttings_spare": sch.get("cuttings_spare"),
			"pool_steps": sch.get("pool_steps"),
			"weeks_met": sch["weeks_met"],
			"weeks": sch["weeks"],
			"shortfall": sch["shortfall"],
			"covers": sch["covers"],
			"cutting_weeks": sch["pick"]["cutting_weeks"],
			"idle_weeks": sch.get("weeks_idle_before_demand"),
			"batches": sch["pick"].get("batches"),
			"steps": sch["pick"].get("steps"),
			"generations": sch["generations"],
			"weeks_table": sch["weeks_table"],
			"order_late": getdate(sch["order_by"]) < getdate(nowdate()),
		}
		# What multiplying buys us, in the only currency that matters here: TC
		# plants not ordered. Buying the whole pool outright is the comparison,
		# because that is what a plan with no multiplication does.
		opts = sizing["options"]
		flat = next((o for o in opts if not cint(o.get("cycles"))), None)
		pick_o = opts[sizing["suggested"]]
		if flat and cint(pick_o.get("buy")):
			out["multiplying_saves"] = {
				"tc_without_multiplying": cint(flat["buy"]),
				"tc_with_multiplying": cint(pick_o["buy"]),
				"tc_saved": max(0, cint(flat["buy"]) - cint(pick_o["buy"])),
				"times_fewer": round(flt(flat["buy"]) / flt(pick_o["buy"]), 1),
				"multiplications": cint(pick_o.get("cycles")),
				# Multiplying costs time, and the time is the order week. Both
				# are stated so the saving is never read on its own.
				"order_by_without": flat.get("order_by"),
				"order_by_with": pick_o.get("order_by"),
				"order_late": (getdate(pick_o["order_by"]) < getdate(nowdate())
				               if pick_o.get("order_by") else None),
			}
		out["recommended"] = sizing["options"][sizing["suggested"]]
		out["options"] = sizing["options"]
		out["peak"] = {
			"cuttings": sizing["peak_cuttings"],
			"week": sizing["peak_week"],
			"week_label": sizing["peak_week_label"],
			"per_mother_per_week": sizing["cuttings_per_mother_per_week"],
			"min_planting_area": sizing["min_planting_area"],
			"suggested": sizing["suggested"],
			# The cap that applied: the protocol's where it states one, the
			"max": cint(sizing.get("max_cycles")) or ps.max_multiplications(),
			"farm_max": ps.max_multiplications(),
		}

	out["saved"] = saved
	return out


@frappe.whitelist()
def record_propagation_actual(propagation_plan, event, actual_date, units=0,
                              plants=0, against_week=None, reference=None,
                              supplier=None, warehouse=None, reason=None,
                              remarks=None):
	"""Record one thing that happened at the unit, from the planning page."""
	_guard()
	a = frappe.new_doc("Summer Flower Propagation Actual")
	a.propagation_plan = propagation_plan
	a.event = event
	a.actual_date = actual_date
	a.against_week = against_week or None
	a.units = cint(units)
	a.plants = cint(plants)
	a.reference = reference or None
	a.supplier = supplier or None
	a.warehouse = warehouse or None
	a.reason = reason or None
	a.remarks = remarks or None
	a.insert()
	a.submit()
	return {"name": a.name, "event": a.event, "date": str(a.actual_date)}


@frappe.whitelist()
def accept_propagation_replan(propagation_plan):
	"""Take the re-plan in, from the planning page."""
	from upande_summer_flowers.summer_flowers import propagation_actuals as pa

	_guard()
	return pa.accept(propagation_plan)


# ---------------------------------------------------------- the unit's calendar
PROP_EVENT_TYPES = {
	"tc_order": "Order tissue culture",
	"tc_arrive": "Tissue culture on farm",
	"consignment": "Consignment due",
	"consignment_ready": "Consignment ready to work",
	"stick": "Sticking",
	"root": "Rooting done, pot up",
	"nursery": "Out of the nursery",
	"harden": "Hardening",
	"to_farm": "To the farm",
}


@frappe.whitelist()
def propagation_varieties(farm=None, season=None):
	"""Varieties that have a propagation plan, for the filter on the plans list."""
	_guard()
	filters = {"status": ["!=", "Rejected"]}
	if farm:
		filters["farm"] = farm
	if season:
		filters["season"] = season
	rows = frappe.get_all(
		"Summer Flower Propagation Plan", filters=filters,
		fields=["variety", "farm", "season", "name", "status",
		        "total_plants_to_stick", "first_sticking_date"],
		order_by="season_start_year desc")
	out = {}
	for r in rows:
		if not r.variety:
			continue
		v = out.setdefault(r.variety, {
			"variety": r.variety,
			"variety_name": frappe.get_cached_value("Item", r.variety, "item_name"),
			"plans": 0, "plants_to_stick": 0, "farms": set(),
			"next_sticking": None})
		v["plans"] += 1
		v["plants_to_stick"] += cint(r.total_plants_to_stick)
		if r.farm:
			v["farms"].add(r.farm)
		if r.first_sticking_date:
			d = str(r.first_sticking_date)
			if not v["next_sticking"] or d < v["next_sticking"]:
				v["next_sticking"] = d
	for v in out.values():
		v["farms"] = sorted(v["farms"])
	return sorted(out.values(), key=lambda v: (v["variety_name"] or v["variety"]))


@frappe.whitelist()
def propagation_schedule(propagation_plan=None, plan=None, variety=None,
                         farm=None, from_date=None, weeks=None):
	"""Every dated thing the unit has to do for one variety, in order."""
	_guard()
	detail = propagation_detail(plan=plan, propagation_plan=propagation_plan,
	                            variety=variety, farm=farm)
	if not detail.get("propagation_plan"):
		return dict(detail, events=[], by_week=[], phases=[])

	version = detail.get("protocol")
	spec = frappe.db.get_value(
		"Crop Protocol Version", version,
		["weeks_on_tray", "weeks_on_pot", "hardening_weeks", "weeks_to_max_pc",
		 "sticking_to_planting_weeks", "establishment_weeks", "cycle_time_weeks",
		 "route_summary", "max_multiplication_cycles"],
		as_dict=True) if version else frappe._dict()
	spec = spec or frappe._dict()

	tray = cint(spec.get("weeks_on_tray"))
	pot = cint(spec.get("weeks_on_pot"))
	harden = cint(spec.get("hardening_weeks"))
	to_plant = cint(spec.get("sticking_to_planting_weeks")) or (tray + pot + harden)

	events = []

	def add(date, kind, title, qty=None, note=None, source="derived", ref=None):
		if not date:
			return
		d = getdate(date)
		if from_date and d < getdate(from_date):
			return
		y, w = iso_year_week(d)
		events.append({
			"date": str(d), "iso_year": y, "week_no": w,
			"week_label": "%s-W%02d" % (y, w),
			"kind": kind, "kind_label": PROP_EVENT_TYPES.get(kind, kind),
			"title": title, "qty": cint(qty) if qty else None,
			"note": note, "source": source, "ref": ref,
		})

	t = detail["totals"]
	add(t.get("tc_order_date"), "tc_order",
	    _("Order {0} tissue culture plants").format(t.get("tc_plants") or 0),
	    qty=t.get("tc_plants"), source="plan")
	add(t.get("tc_on_farm_date"), "tc_arrive",
	    _("Tissue culture due on farm"), qty=t.get("tc_plants"), source="plan")

	for c in detail.get("consignments", []):
		label = c.get("form") or _("material")
		add(c.get("week"), "consignment",
		    _("{0}: {1} due").format(label, "{:,}".format(cint(c.get("plants")))),
		    qty=c.get("plants"),
		    note=(_("{0} actually arrived on {1}").format(
			    "{:,}".format(cint(c.get("actual_units"))), c.get("actual_on"))
		          if c.get("actual_on") else c.get("state")),
		    source="plan", ref=c.get("reference"))
		add(c.get("ready_on"), "consignment_ready",
		    _("{0} ready to work").format(label), qty=c.get("plants"),
		    source="plan")

	for w in detail.get("weeks", []):
		stick_on = w.get("week_start_date")
		plants = cint(w.get("plants_to_stick"))
		if not stick_on or not plants:
			continue
		add(stick_on, "stick",
		    _("Stick {0} cuttings").format("{:,}".format(cint(w.get("cuttings_required")) or plants)),
		    qty=plants,
		    note=(_("{0} delivered").format("{:,}".format(cint(w.get("delivered"))))
		          if w.get("delivered") else None),
		    source="plan", ref=w.get("label"))
		if tray:
			add(add_days(stick_on, tray * 7), "root",
			    _("Rooting done -- pot up {0}").format("{:,}".format(plants)),
			    qty=plants,
			    note=_("{0} weeks on the tray").format(tray), ref=w.get("label"))
		if pot:
			add(add_days(stick_on, (tray + pot) * 7), "nursery",
			    _("Out of the nursery"), qty=plants,
			    note=_("{0} weeks on the pot").format(pot), ref=w.get("label"))
		if harden:
			add(add_days(stick_on, (tray + pot) * 7), "harden",
			    _("Hardening starts"), qty=plants,
			    note=_("{0} weeks hardening off").format(harden), ref=w.get("label"))
		add(w.get("deliver_on") or (add_days(stick_on, to_plant * 7) if to_plant else None),
		    "to_farm", _("{0} plants to the farm").format("{:,}".format(plants)),
		    qty=plants, source="plan" if w.get("deliver_on") else "derived",
		    ref=w.get("label"))

	# The motherstock line the cuttings come off. Without these the calendar
	ms = motherstock_plan(variety=detail.get("variety"), farm=detail.get("farm"),
	                      propagation_plan=detail.get("propagation_plan"))
	for s in ms.get("steps", []):
		add(s.get("target_date"), s.get("kind"), s.get("step"),
		    qty=s.get("mother_plants"), note=s.get("notes"),
		    source="motherstock", ref=s.get("batch"))

	events.sort(key=lambda e: (e["date"], e["kind"]))
	if weeks:
		cutoff = str(add_days(getdate(from_date or nowdate()), cint(weeks) * 7))
		events = [e for e in events if e["date"] <= cutoff]

	by_week = {}
	for e in events:
		by_week.setdefault(e["week_label"], []).append(e)

	phases = [
		{"phase": _("On the tray"), "weeks": tray,
		 "note": _("sticking to rooted")},
		{"phase": _("On the pot"), "weeks": pot,
		 "note": _("rooted to nursery-grown")},
		{"phase": _("Hardening"), "weeks": harden,
		 "note": _("nursery to field-ready")},
		{"phase": _("Sticking to planting"), "weeks": to_plant,
		 "note": _("the whole establishment")},
	]

	return dict(detail,
	            events=events,
	            by_week=[{"week": k, "events": v} for k, v in
	                     sorted(by_week.items())],
	            phases=[p for p in phases if p["weeks"]],
	            route_summary=spec.get("route_summary"),
	            cycle_time_weeks=cint(spec.get("cycle_time_weeks")),
	            max_cycles=cint(spec.get("max_multiplication_cycles")),
	            motherstock=ms.get("batches", []),
	            motherstock_reason=ms.get("reason"),
	            kinds=dict(PROP_EVENT_TYPES, **MS_EVENT_TYPES))


# ---------------------------------------------------------------------------

MS_EVENT_TYPES = {
	"ms_tc_order": "Order motherstock TC",
	"ms_tc_arrive": "Motherstock TC on farm",
	"ms_cuttable": "Motherstock cuttable",
	"ms_multiply": "Multiply motherstock",
	"ms_productive": "Multiplied generation productive",
	"ms_first_cut": "First cutting",
	"ms_full": "Full cutting capacity",
	"ms_renew": "Order renewal TC",
	"ms_expire": "Motherstock expires",
}

# The step labels build_steps() writes, mapped onto the event kinds above. The
_MS_STEP_KINDS = (
	("Place TC order", "ms_tc_order"),
	("TC plants on farm", "ms_tc_arrive"),
	("TC generation cuttable", "ms_cuttable"),
	("Multiplication cycle", "ms_multiply"),
	("Multiplied generation productive", "ms_productive"),
	("First cutting", "ms_first_cut"),
	("Full cutting capacity", "ms_full"),
	("Order renewal TC", "ms_renew"),
	("Motherstock expires", "ms_expire"),
)


def _ms_kind(step_label):
	for needle, kind in _MS_STEP_KINDS:
		if needle.lower() in (step_label or "").lower():
			return kind
	return "ms_multiply"


def _motherstock_batches(variety=None, farm=None, production_plan=None):
	"""The motherstock lines a unit would be cutting for this variety."""
	base = {"docstatus": ["<", 2]}
	if variety:
		base["variety"] = variety

	def fetch(filters):
		return frappe.get_all(
		"Summer Flower Motherstock Batch", filters=filters,
		fields=["name", "variety", "farm", "batch_status", "generation",
		        "mother_plants", "pots_required", "bench_sqm",
		        "effective_peak_cuttings", "peak_weekly_cuttings",
		        "build_up_cycles", "multiplication_factor",
		        "tc_plants_required", "tc_order_date", "tc_on_farm_date",
		        "first_sticking_date", "line_start_date", "max_pc_date",
		        "productive_weeks", "expiry_date", "renewal_tc_order_date",
		        "ramp_weeks", "schedule_warning", "protocol"],
		order_by="first_sticking_date asc, name asc")

	if farm:
		narrowed = fetch(dict(base, farm=farm))
		if narrowed:
			return narrowed
	return fetch(base)


@frappe.whitelist()
def motherstock_plan(variety=None, farm=None, plan=None, propagation_plan=None):
	"""How the cuttings for this variety are raised, and when they are cut."""
	_guard()
	if propagation_plan:
		pp = frappe.db.get_value("Summer Flower Propagation Plan", propagation_plan,
		                         ["variety", "farm"], as_dict=True)
		if pp:
			variety = variety or pp.variety
			farm = farm or pp.farm

	batches = _motherstock_batches(variety, farm)
	# A variety with no motherstock line is bought-in, not broken. Say so
	if not batches:
		return {"variety": variety, "farm": farm, "batches": [],
		        "steps": [], "reason": "no motherstock line for this variety"}

	out = []
	for b in batches:
		steps = frappe.get_all(
			"Summer Flower Motherstock Cycle",
			filters={"parent": b.name, "parenttype": "Summer Flower Motherstock Batch"},
			fields=["step", "week_label", "target_date", "mother_plants", "pots", "notes"],
			order_by="idx asc")
		for s in steps:
			s["kind"] = _ms_kind(s.get("step"))
			s["kind_label"] = MS_EVENT_TYPES.get(s["kind"], s["kind"])
			s["batch"] = b.name
			s["done"] = bool(s.get("target_date")
			                 and getdate(s["target_date"]) <= getdate(nowdate()))
		b["steps"] = steps
		b["cutting_from"] = b.get("first_sticking_date") or b.get("line_start_date")
		b["cuttings_per_week"] = cint(b.get("effective_peak_cuttings"))
		out.append(b)

	flat = [s for b in out for s in b["steps"] if s.get("target_date")]
	flat.sort(key=lambda s: str(s["target_date"]))

	return {"variety": variety, "farm": farm, "batches": out, "steps": flat,
	        "kinds": MS_EVENT_TYPES}


DELIVERY_STATES = ("Planned", "Partially Delivered", "Delivered", "Closed")


@frappe.whitelist()
def delivery_schedule(farm=None, variety=None, from_date=None, weeks=None,
                      include_past=None):
	"""What leaves for the farms, when, and whether it has gone yet."""
	_guard()
	start = getdate(from_date or nowdate())
	if not cint(include_past):
		# Last week is still worth seeing -- a Monday delivery slips to Tuesday
		start = add_days(start, -7)
	horizon = add_days(start, cint(weeks or 12) * 7)

	plan_filters = {"status": "Approved", "docstatus": ["<", 2]}
	if farm:
		plan_filters["farm"] = farm
	if variety:
		plan_filters["variety"] = variety
	plans = frappe.get_all("Summer Flower Propagation Plan", filters=plan_filters,
	                       fields=["name", "variety", "farm", "season", "protocol"])

	def empty(reason):
		# An empty lorry list and a broken screen look identical unless the
		return {"weeks": [], "rows": [], "totals": {
			        "plants": 0, "delivered": 0, "outstanding": 0, "weeks": 0},
		        "farms": [], "varieties": [], "reason": reason,
		        "from_date": str(start), "to_date": str(horizon)}

	if not plans:
		unapproved = frappe.db.count("Summer Flower Propagation Plan",
		                             {"status": ["!=", "Approved"], "docstatus": ["<", 2]})
		if unapproved:
			return empty(_("No approved plan yet. {0} plan(s) are still drafts -- "
			               "nothing is delivered off a draft.").format(unapproved))
		return empty(_("No propagation plan covers this farm or variety."))

	by_name = {p.name: p for p in plans}
	rows = frappe.get_all(
		"Summer Flower Propagation Week",
		filters={"parent": ["in", list(by_name)],
		         "parenttype": "Summer Flower Propagation Plan",
		         "deliver_on": ["between", [str(start), str(horizon)]]},
		fields=["parent", "year", "week_no", "deliver_on", "week_start_date",
		        "plants_to_stick", "baseline_plants", "actual_plants",
		        "actual_on", "variance_plants", "state", "plant_week",
		        "from_existing_ms", "from_new_ms", "from_bought", "notes"],
		order_by="deliver_on asc")

	out = []
	for r in rows:
		p = by_name.get(r.parent)
		if not p:
			continue
		due = cint(r.plants_to_stick)
		got = cint(r.actual_plants)
		y, w = iso_year_week(getdate(r.deliver_on))
		out.append({
			"plan": r.parent, "variety": p.variety, "farm": p.farm,
			"season": p.season, "protocol": p.protocol,
			"deliver_on": str(r.deliver_on),
			"week_label": f"{y}-W{w:02d}",
			"stuck_in_week": str(r.week_start_date) if r.week_start_date else None,
			"plant_week": r.plant_week,
			"plants": due,
			"delivered": got,
			"outstanding": max(0, due - got),
			"variance": cint(r.variance_plants),
			"state": r.state or "Planned",
			"delivered_on": str(r.actual_on) if r.actual_on else None,
			# Where this week's plants come from: the unit's own standing
			"source": {
				"existing_motherstock": cint(r.from_existing_ms),
				"new_motherstock": cint(r.from_new_ms),
				"bought": cint(r.from_bought),
			},
			"notes": r.notes,
		})

	if not out:
		return empty(_("Approved plans exist, but none has a delivery due "
		               "between {0} and {1}.").format(start, horizon))

	by_week = {}
	for r in out:
		by_week.setdefault(r["week_label"], []).append(r)

	weeks_out = []
	for label in sorted(by_week):
		items = by_week[label]
		weeks_out.append({
			"week": label,
			"deliver_on": min(i["deliver_on"] for i in items),
			"farms": sorted({i["farm"] for i in items if i["farm"]}),
			"plants": sum(i["plants"] for i in items),
			"delivered": sum(i["delivered"] for i in items),
			"outstanding": sum(i["outstanding"] for i in items),
			"rows": items,
		})

	return {
		"weeks": weeks_out,
		"rows": out,
		"from_date": str(start),
		"to_date": str(horizon),
		"totals": {
			"plants": sum(r["plants"] for r in out),
			"delivered": sum(r["delivered"] for r in out),
			"outstanding": sum(r["outstanding"] for r in out),
			"weeks": len(weeks_out),
		},
		"farms": sorted({r["farm"] for r in out if r["farm"]}),
		"varieties": sorted({r["variety"] for r in out if r["variety"]}),
	}


# ---------------------------------------------------------------------------

def _trough_calendar(production_plan, proc, version):
	"""When this plan's roots come off the troughs, per unit."""
	try:
		from upande_propagation import allocation as A
	except ImportError:
		return None
	units = [a["propagation_unit"] for a in proc.get("allocated") or []
	         if a.get("propagation_unit")]
	if not units:
		return None
	try:
		cal = A.delivery_calendar(production_plan, units=units,
		                          form=proc.get("entry_stage") or "Roots",
		                          version=version)
	except Exception:
		# A missing protocol lift row or a trough mid-edit must not take the
		frappe.log_error(frappe.get_traceback(), "farm_plan trough calendar")
		return None
	return cal if cal.get("units") else None


@frappe.whitelist()
def farm_plan(farm, variety=None, from_date=None, weeks=None, include_past=None):
	"""What the farm expects to receive, week by week, and where each lot is raised."""
	_guard()
	start = getdate(from_date or nowdate())
	if not cint(include_past):
		start = add_days(start, -7)
	horizon = add_days(start, cint(weeks or 26) * 7)

	pfilters = {"farm": farm, "docstatus": ["<", 2]}
	if variety:
		pfilters["variety"] = variety
	plans = frappe.get_all("Summer Flower Production Plan", filters=pfilters,
	                       fields=["name", "variety", "season", "status", "protocol"])
	if not plans:
		return {"farm": farm, "weeks": [], "totals": {}, "sources": [],
		        "reason": _("No production plan for {0}.").format(farm)}

	by_plan = {p.name: p for p in plans}

	# Who raises what, per plan: the allocation rows, and the shortfall that is
	raisers = {}
	for proc in frappe.get_all("Summer Flower Procurement Plan",
	                           filters={"production_plan": ["in", list(by_plan)],
	                                    "docstatus": ["<", 2]},
	                           fields=["name", "production_plan", "entry_stage",
	                                   "method", "supplier"]):
		rows = frappe.get_all("Summer Flower Propagation Allocation",
		                      filters={"parent": proc.name},
		                      fields=["propagation_unit", "supplier", "plants",
		                              "propagation_plan"])
		# Our own units and outside propagators are kept apart: a supplier row
		raisers.setdefault(proc.production_plan, []).append({
			"procurement_plan": proc.name, "entry_stage": proc.entry_stage,
			"method": proc.method, "supplier": proc.supplier,
			"allocated": [dict(r) for r in rows if r.propagation_unit],
			"outside": [dict(r) for r in rows if r.supplier and not r.propagation_unit],
			"own_plants": sum(cint(r.plants) for r in rows if r.propagation_unit),
		})

	# ── the trough calendar, first ─────────────────────────────────────────
	calendars, lots = {}, []
	for p in plans:
		for proc in raisers.get(p.name, []):
			if (proc.get("entry_stage") or "") != "Roots":
				continue
			cal = _trough_calendar(p.name, proc, p.protocol)
			if cal:
				calendars[p.name] = (cal, proc)
				break

	weeks_out = {}

	def slot_for(when):
		d = getdate(when)
		key = "%d-W%02d" % (d.isocalendar()[0], d.isocalendar()[1])
		return key, weeks_out.setdefault(key, {
			"week": key, "deliver_on": str(iso_monday(*[int(x) for x in
			                                           key.replace("W", "").split("-")])),
			"plants": 0, "varieties": set(), "sources": {}, "from_calendar": False})

	def add(when, who, qty, variety, kind, from_calendar):
		qty = cint(qty)
		if qty <= 0 or not (getdate(start) <= getdate(when) <= getdate(horizon)):
			return
		_key, slot = slot_for(when)
		slot["plants"] += qty
		slot["varieties"].add(variety)
		slot["from_calendar"] = slot["from_calendar"] or from_calendar
		cur = slot["sources"].setdefault(who, {"plants": 0, "kind": kind})
		cur["plants"] += qty

	for p in plans:
		if p.name in calendars:
			cal, proc = calendars[p.name]
			# Our own units: the lift dates, in trough-sized lots.
			for u in cal["units"]:
				for on, qty in u["by_week"].items():
					add(on, u["unit"], qty, p.variety, "trough lift", True)
				lots.extend([dict(x, unit=u["unit"], variety=p.variety)
				             for x in u["rows"]
				             if getdate(start) <= getdate(x["ready_on"]) <= getdate(horizon)])
			# What no trough could hold is bought as plants, and those land on
			who = proc.get("supplier") or _("bought in")
			for on, qty in (cal.get("bought_in") or {}).items():
				add(on, who, qty, p.variety, "bought in", True)
			continue

		# No trough answer for this plan: fall back to the allocation share,
		rows = frappe.get_all(
			"Summer Flower Plan Block",
			filters={"parent": p.name, "is_new_planting": 1},
			fields=["planting_year", "planting_week", "plants"])
		mine = raisers.get(p.name, [])
		own_total = sum(r["own_plants"] for r in mine)
		plan_total = sum(cint(r.plants) for r in rows) or 1
		own_share = min(1.0, own_total / float(plan_total))

		for r in rows:
			y, w = cint(r.planting_year), cint(r.planting_week)
			if not (y and w):
				continue
			when = iso_monday(y, w)
			qty = cint(r.plants)
			own = int(round(qty * own_share))
			for a in mine:
				for alloc in a["allocated"]:
					share = (cint(alloc["plants"]) / float(own_total)) if own_total else 0
					add(when, alloc["propagation_unit"], int(round(own * share)),
					    p.variety, "allocated share", False)
			outside = 0
			for a in mine:
				for alloc in a.get("outside") or []:
					theirs = int(round(qty * min(1.0, cint(alloc["plants"]) / float(plan_total))))
					outside += theirs
					add(when, alloc["supplier"], theirs, p.variety, "outside propagator", False)
			bought = qty - own - outside
			if bought > 0:
				who = next((a["supplier"] for a in mine if a.get("supplier")), None) \
					or _("bought in")
				add(when, who, bought, p.variety, "bought in", False)

	out = []
	for key in sorted(weeks_out):
		s = weeks_out[key]
		out.append({
			"week": s["week"], "deliver_on": s["deliver_on"], "plants": s["plants"],
			"varieties": sorted(s["varieties"]),
			# Whether this week is what the troughs will actually hand over, or
			"from_calendar": bool(s["from_calendar"]),
			"sources": [{"name": k, "plants": v["plants"], "kind": v["kind"]}
			            for k, v in sorted(s["sources"].items(),
			                               key=lambda kv: -kv[1]["plants"])
			            if v["plants"] > 0],
		})

	own_names = {a["propagation_unit"] for v in raisers.values() for x in v
	             for a in x["allocated"] if a.get("propagation_unit")}
	return {
		"farm": farm,
		"from_date": str(start), "to_date": str(horizon),
		"weeks": out,
		"own_units": sorted(own_names),
		"totals": {
			"plants": sum(w["plants"] for w in out),
			"weeks": len(out),
			"from_own_units": sum(s["plants"] for w in out for s in w["sources"]
			                      if s["name"] in own_names),
			"from_troughs": sum(s["plants"] for w in out for s in w["sources"]
			                    if s["kind"] == "trough lift"),
			"bought_in": sum(s["plants"] for w in out for s in w["sources"]
			                 if s["kind"] == "bought in"),
			# What the plan asked for, against what the troughs make it. They
			"demand": sum(cint(c[0]["season_plants"]) for c in calendars.values()),
			"surplus": sum(cint(u["surplus"]) for c in calendars.values()
			               for u in c[0]["units"]),
		},
		"varieties": sorted({v for w in out for v in w["varieties"]}),
		# Every trough lot landing in the window: which trough, what went into
		"lots": sorted(lots, key=lambda x: (x["ready_on"], x.get("unit") or "")),
		"calendar_weeks": len([w for w in out if w["from_calendar"]]),
		"plans_on_calendar": sorted(calendars),
	}



def _unit_type():
	from upande_summer_flowers.summer_flowers.settings import propagation_unit_type

	return propagation_unit_type()


def _stem_price(variety):
	from upande_summer_flowers.summer_flowers.valuation import stem_price

	return stem_price(variety)

# ---------------------------------------------------------------------------

@frappe.whitelist()
def seller_choices(procurement_plan):
	"""The ways this plan buys, and who could sell each of them."""
	_guard()
	doc = frappe.get_doc("Summer Flower Procurement Plan", procurement_plan)
	frappe.has_permission("Summer Flower Procurement Plan", "read", doc, throw=True)
	stages = list(doc.units_by_stage() or {})
	if not stages:
		stages = [doc.main_entry_stage() or doc.entry_stage or ""]
	# What each way in comes to, so a share can be read as a quantity. A share
	by_stage = doc.units_by_stage() or {}
	return {
		"plan": doc.name,
		"editable": 1 if doc.docstatus == 0 else 0,
		"stages": [s for s in stages if s],
		"units_by_stage": {k: cint(v) for k, v in by_stage.items()},
		"unit_label": doc.entry_stage or _("units"),
		"suppliers": frappe.get_all("Supplier", filters={"disabled": 0},
		                            pluck="name", order_by="name", limit=500),
		"rows": [{"entry_stage": r.entry_stage, "supplier": r.supplier,
		          "share_pct": flt(r.share_pct), "rate": flt(r.rate),
		          "units": int(round(cint(by_stage.get(r.entry_stage))
		                             * flt(r.share_pct) / 100.0)),
		          "amount": round(int(round(cint(by_stage.get(r.entry_stage))
		                                    * flt(r.share_pct) / 100.0)) * flt(r.rate), 2)}
		         for r in (doc.get("purchase_allocation") or [])],
	}


@frappe.whitelist()
def save_sellers(procurement_plan, rows):
	"""Who sells each way in, written from the dashboard."""
	_guard()
	if isinstance(rows, str):
		rows = frappe.parse_json(rows)
	doc = frappe.get_doc("Summer Flower Procurement Plan", procurement_plan)
	frappe.has_permission("Summer Flower Procurement Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is not a draft, so who sells it is already settled.")
		             .format(doc.name))
	doc.set("purchase_allocation", [])
	for r in (rows or []):
		if not r.get("supplier"):
			continue
		doc.append("purchase_allocation", {
			"entry_stage": r.get("entry_stage") or doc.entry_stage,
			"supplier": r.get("supplier"),
			"share_pct": flt(r.get("share_pct")),
			"rate": flt(r.get("rate")),
			"lead_weeks": cint(doc.lead_weeks),
		})
	doc.flags.ignore_permissions = True
	# Saying who sells it is not saying who raises it, and the plan's guard on
	doc.flags.choosing_sellers = True
	doc.save()
	return seller_choices(doc.name)


@frappe.whitelist()
def propagator_choices():
	"""Our own propagation units (warehouses of type Propagation Unit) and suppliers."""
	return {
		"units": frappe.get_all("Warehouse", filters={"warehouse_type": _unit_type(),
		                                              "is_group": 0, "disabled": 0},
		                        pluck="name", order_by="name"),
		"suppliers": frappe.get_all("Supplier", filters={"disabled": 0}, pluck="name",
		                            order_by="name"),
	}


def _draft_procurement(name):
	doc = frappe.get_doc("Summer Flower Procurement Plan", name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("{0} is already approved; who raises it was settled then.").format(name))
	return doc


@frappe.whitelist()
def propose_propagators(procurement_plan):
	"""Allocate the season across our own units by what their troughs hold."""
	return _draft_procurement(procurement_plan).propose_allocation()


@frappe.whitelist()
def save_propagators(procurement_plan, rows):
	"""Allocate the season by the shares chosen on the board."""
	rows = frappe.parse_json(rows) if isinstance(rows, str) else (rows or [])
	units = [{"unit": r["propagation_unit"], "share_pct": flt(r.get("share_pct"))}
	         for r in rows if r.get("propagation_unit")]
	suppliers = [{"supplier": r["supplier"], "share_pct": flt(r.get("share_pct")),
	              "rate_per_plant": flt(r.get("rate_per_plant"))}
	             for r in rows if r.get("supplier")]
	if not units and not suppliers:
		frappe.throw(_("Choose at least one propagation unit or outside propagator."))
	total = sum(x["share_pct"] for x in units + suppliers)
	if abs(total - 100) > 0.01:
		frappe.throw(_("The shares come to {0}%. They have to make the whole season, 100%.")
		             .format(round(total, 2)))
	return _draft_procurement(procurement_plan).propose_allocation(units=units,
	                                                                suppliers=suppliers)


@frappe.whitelist()
def allocation_plan(procurement_plan):
	"""What each allocated raiser has to do, week by week.

	Our own unit gets a propagation plan: start, material in, plants handed to
	the field. An outside propagator gets a delivery schedule: order by, drop
	on, plants delivered. Every plant figure is after the losses the protocol
	states, because that is what the plan is sized on."""
	_guard()
	doc = frappe.get_doc("Summer Flower Procurement Plan", procurement_plan)
	frappe.has_permission("Summer Flower Procurement Plan", "read", doc, throw=True)

	# What each cohort puts in the ground, from the production plan it came from.
	to_plant = {}
	if doc.production_plan:
		plan = frappe.get_doc("Summer Flower Production Plan", doc.production_plan)
		for b in plan.plan_blocks:
			to_plant[b.name] = (cint(b.get("plants_to_plant")) or cint(b.plants))

	raisers = []
	for a in (doc.get("propagation_allocation") or []):
		share = flt(a.share_pct) / 100.0
		outside = a.alloc_type == "Outside Propagator"
		weeks, units_t, hand_t, stand_t = [], 0, 0, 0
		for r in doc.requirements:
			if (a.raises or "") and (r.entry_stage or "") != a.raises:
				continue
			when = getdate(r.planting_date or r.required_at_site_date)
			if not when:
				continue
			units = int(round(cint(r.qty_to_order) * share))
			stand = int(round(cint(r.qty_at_field) * share))
			hand = int(round(cint(to_plant.get(r.cohort, 0)) * share)) or stand
			start = add_days(when, -7 * cint(a.lead_weeks))
			units_t += units
			hand_t += hand
			stand_t += stand
			weeks.append({
				"cohort": r.cohort,
				"start_iso": _iso_week(start), "start_date": str(start),
				"wanted_iso": _iso_week(when), "wanted_date": str(when),
				# What has to be started with, what is handed over, and what is
				# still standing once it has established. Three different numbers
				# that a single "plants" column used to be asked to be.
				"units": units, "unit_name": r.entry_stage or doc.entry_stage,
				"plants_handed": hand, "plants_standing": stand,
				"amount": flt(a.rate_per_plant) * hand if outside else 0,
				"late": 1 if start < getdate(nowdate()) else 0,
			})
		weeks.sort(key=lambda x: x["wanted_date"])
		raisers.append({
			"party": a.party or a.supplier or a.propagation_unit,
			"outside": 1 if outside else 0,
			"raises": a.raises, "share_pct": flt(a.share_pct),
			"lead_weeks": cint(a.lead_weeks),
			"rate_per_plant": flt(a.rate_per_plant),
			"propagation_plan": a.propagation_plan,
			"purchase_order": a.purchase_order,
			"basis": a.ready_basis,
			"weeks": weeks,
			"units_total": units_t,
			"plants_handed_total": hand_t,
			"plants_standing_total": stand_t,
			"amount_total": flt(a.rate_per_plant) * hand_t if outside else 0,
			"starts_late": sum(1 for w in weeks if w["late"]),
		})
	return {
		"procurement_plan": doc.name,
		"production_plan": doc.production_plan,
		"entry_stage": doc.entry_stage,
		"currency": frappe.db.get_value("Company", doc.get("company"),
		                                "default_currency") if doc.get("company") else None,
		"raisers": raisers,
	}


def _iso_week(d):
	if not d:
		return None
	y, w, _dow = getdate(d).isocalendar()
	return "%s-W%02d" % (y, w)


@frappe.whitelist()
def suppliers_for(form=None):
	"""Suppliers and the cycles they hand a consignment over in."""
	_guard()
	from upande_summer_flowers.summer_flowers import supply_cycles

	return supply_cycles.suppliers_for(form or supply_cycles.DEFAULT_FORM)


@frappe.whitelist()
def plan_supply(plan=None, picks=None, variety=None, farm=None, form=None):
	"""What a chosen set of suppliers, on their own cycles, has to be ordered."""
	_guard()
	from upande_summer_flowers.summer_flowers import supply_cycles

	name = resolve_plan(variety, farm, plan)
	if not name:
		return {"plan": None, "weeks": [], "reason": "No plan in scope."}
	return supply_cycles.plan_supply(name, picks=picks, form=form)


@frappe.whitelist()
def save_supply(plan=None, picks=None, confirm=0, variety=None, farm=None):
	"""Agree the suppliers and cycles the plan will be grown from."""
	_guard()
	from upande_summer_flowers.summer_flowers import supply_cycles

	name = resolve_plan(variety, farm, plan)
	if not name:
		frappe.throw(_("No plan in scope."))
	return supply_cycles.save_supply(name, picks, confirm=confirm)


@frappe.whitelist()
def clear_supply(plan=None, variety=None, farm=None):
	"""Un-agree the supply so it can be chosen again."""
	_guard()
	from upande_summer_flowers.summer_flowers import supply_cycles

	return supply_cycles.clear_supply(resolve_plan(variety, farm, plan))


@frappe.whitelist()
def saved_supply(plan=None, variety=None, farm=None):
	"""The supply this plan has already agreed to."""
	_guard()
	from upande_summer_flowers.summer_flowers import supply_cycles

	name = resolve_plan(variety, farm, plan)
	return supply_cycles.saved_supply(name) if name else {"picks": [], "confirmed": 0}


@frappe.whitelist()
def save_delivery(plan=None, week_iso=None, units=None, supplier=None, note=None,
                  row=None, variety=None, farm=None):
	"""Add or change a delivery typed in against a week."""
	_guard()
	from upande_summer_flowers.summer_flowers import supply_cycles

	name = resolve_plan(variety, farm, plan)
	return supply_cycles.save_delivery(name, week_iso, units, supplier=supplier,
	                                   note=note, row=row)


@frappe.whitelist()
def delete_delivery(plan=None, row=None, variety=None, farm=None):
	"""Take a typed-in delivery off the plan."""
	_guard()
	from upande_summer_flowers.summer_flowers import supply_cycles

	return supply_cycles.delete_delivery(resolve_plan(variety, farm, plan), row)


@frappe.whitelist()
def pull_procurement(plan=None, variety=None, farm=None):
	"""Bring what the sellers and raisers agreed to onto the plan."""
	_guard()
	from upande_summer_flowers.summer_flowers import supply_cycles

	name = resolve_plan(variety, farm, plan)
	return supply_cycles.pull_procurement(name) if name else {"plan": None}


@frappe.whitelist()
def agreed_record(plan=None, variety=None, farm=None):
	"""What the plan has on file: the schedule confirmed and who raises it."""
	_guard()
	name = resolve_plan(variety, farm, plan)
	if not name:
		return {"plan": None, "orders": [], "raisers": []}
	d = frappe.get_doc("Summer Flower Production Plan", name)
	# Read from the procurement plan itself. The plan used to keep a copy of
	# the schedule, which was a second place for the same facts to go stale.
	proc = frappe.get_all("Summer Flower Procurement Plan",
	                      filters={"production_plan": d.name, "docstatus": ["<", 2]},
	                      fields=["name", "status"], order_by="creation desc", limit=1)
	proc = proc[0] if proc else None
	orders, asked, confirmed = [], 0, 0
	if proc:
		p = frappe.get_doc("Summer Flower Procurement Plan", proc.name)
		for r in p.requirements:
			want = cint(r.qty_to_order)
			got = (cint(r.confirmed_qty) if cint(r.get("delivery_confirmed"))
			       else want)
			asked += want
			confirmed += got
			orders.append({
				"week_iso": _week_label(r.required_at_site_date),
				"supplier": r.supplier or p.supplier,
				"stage": r.entry_stage or p.entry_stage,
				"asked": want, "confirmed": got,
				"delivers": (r.confirmed_delivery_week
				             or _week_label(r.expected_delivery_date)),
				"state": (_("short {0}").format("{:,}".format(want - got))
				          if got < want else _("as asked")),
			})
	return {
		"plan": d.name, "docstatus": d.docstatus, "status": d.status,
		"procurement_plan": proc.name if proc else None,
		"procurement_status": proc.status if proc else _("nothing raised yet"),
		"units_asked": asked, "units_confirmed": confirmed,
		"short": max(0, asked - confirmed),
		"plants": cint(d.get("propagation_plants")),
		"outside_cost": flt(d.get("propagation_cost")),
		"supply_confirmed": cint(d.get("supply_confirmed")),
		"supply_note": d.get("supply_note"),
		"orders": orders,
		"raisers": [{"type": r.alloc_type, "party": r.party, "raises": r.raises,
		             "share_pct": flt(r.share_pct), "plants": cint(r.plants),
		             "rate": flt(r.rate_per_plant), "amount": flt(r.amount),
		             "start_by": str(r.start_by or "") or None,
		             "propagation_plan": r.propagation_plan,
		             "purchase_order": r.purchase_order}
		            for r in (d.get("plan_raisers") or [])],
		"arrivals": [{"week_iso": a.week_iso, "party": a.party,
		              "type": a.alloc_type, "handed": cint(a.plants_handed),
		              "standing": cint(a.plants_standing),
		              "starts": a.start_week}
		             for a in (d.get("plan_arrivals") or [])],
	}


def _week_label(d):
	if not d:
		return None
	y, w, _dow = getdate(d).isocalendar()
	return "%s-W%02d" % (y, w)
