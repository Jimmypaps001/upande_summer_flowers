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
from frappe.utils import add_days, cint, flt, getdate, nowdate

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
	iso_monday,
	iso_year_week,
	week_sequence,
)


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
		        "market_demand", "variety", "farm", "season", "season_start_year",
		        "from_year", "from_week",
		        "to_year", "to_week", "weeks_covered", "total_demand_stems",
		        "total_production_stems", "coverage_pct", "weeks_in_deficit",
		        "new_beds_required", "creation"],
		order_by="creation desc",
	)
	# Every plan carried the same period label, so eighteen of them read as
	# eighteen copies of one thing. Name the crop and the coverage instead: that is
	# what tells them apart.
	for r in rows:
		r["label"] = "%s · %s · %s · %s%s · %.0f%% of demand" % (
			r.name, r.season or _("no season"), r.variety or "?",
			r.workflow_state or r.status or "?",
			" + budget" if r.budget else "", flt(r.coverage_pct))
		r["is_authoritative"] = r.docstatus == 1
	drafts = [r for r in rows if r.docstatus == 0]

	# The financial years the demand register covers, which are not the same as the
	# years there are plans for. A register carrying three years with one of them
	# planned left the year picker holding a single option, and a picker with one
	# option is disabled -- which is what "the financial year is not working" has
	# meant every time it has been reported. The register is per variety and carries
	# no farm, so the farm is deliberately not applied here.
	reg = frappe.get_all("Summer Flower Market Demand",
	                     filters={"variety": variety} if variety else {}, pluck="name")
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
				seasons.add(y if wk >= 27 else y - 1)

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

	# Only a committed order constrains anything, so only then is the stored
	# supplied figure the answer. Reading it unconditionally returns zero for every
	# plan saved before the column existed -- a plan that grows nothing, which is
	# both wrong and alarming. Falling back on the value being falsy would be worse:
	# a week a real order genuinely cannot supply would quietly show production.
	committed = bool(p.tc_choice_committed)

	weeks = [
		{
			"label": f"{w.year}-W{w.week_no:02d}",
			"year": w.year, "week_no": w.week_no, "date": str(w.week_start_date),
			"demand": w.demand_stems or 0,
			"production": w.production_stems or 0,
			"variance": w.variance_stems or 0,
			# What the confirmed order can actually supply this week. Equal to
			# production until an order is confirmed, so the column reads as "no
			# constraint yet" rather than as a plan that grows nothing.
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
		# guessing from the variety name.
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
def tc_derivation(plan=None, variety=None, farm=None):
	"""Show how the TC order size falls out of demand, step by step."""
	_guard()
	dv = demand_vs_production(variety=variety, farm=farm, plan=plan)
	if not dv.get("plan"):
		return {"plan": None}

	v = frappe.get_cached_doc("Crop Protocol Version", dv["version"])
	t = dv["totals"]
	# Sized from the planned peak, so the derivation shows a real order for a plan
	# whose blocks are not settled yet rather than a column of zeros.
	peak_plants = t.get("peak_weekly_sticking_planned") or t["peak_weekly_sticking"] or 0
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
	      "ramp_weeks": 0, "full_capacity_date": "", "lost_to_ramp": 0,
	      # The build-up these figures assume. Without it a caller seeding a
	      # simulator from this payload has to go and ask the protocol
	      # separately, and can seed a quantity and a cycle count that do not
	      # belong to each other.
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
		# cuttings is already cuttings_for_plants(peak) -- rooting and field loss and
		# the reject rate are in it. The batch applies cuttings_per_plant_required
		# itself when apply_losses is set, so leaving it on multiplied the 1.17 in
		# twice: the Motherstock tab read 11,754 plantlets where the TC tab read
		# 10,046, for the same plan and the same week. The same double count produced
		# the 1,992 in the what-if probe, which was fixed there and not here.
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
	# derivation: ordering the requirement exactly arrives short by the loss rate,
	# and seeing only the larger figure makes the multiplication look wrong.
	required = int(math.ceil(v.tc_plants_for(mothers, None, with_loss=False))) if mothers else 0
	mf = v.multiplication_factor()
	factor_txt = ("%g" % mf)
	steps.append({
		"step": "TC plantlets required", "value": required, "unit": "plantlets",
		"note": "%s mother plants / %s, because %s multiplication cycles turn one "
		        "plantlet into %s plants"
		        % (mothers, factor_txt, cint(v.max_multiplication_cycles), factor_txt),
	})
	if flt(v.tc_order_loss_pct):
		steps.append({
			"step": "TC order loss allowance", "value": flt(v.tc_order_loss_pct),
			"unit": "%",
			"note": "plantlets lost between the lab and the bench, so the order is "
			        "the requirement / %.2f" % (1 - flt(v.tc_order_loss_pct) / 100),
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
	tc["build_up_cycles"] = (cint(dv.get("tc_cycles_committed"))
	                         if dv.get("tc_committed")
	                         else cint(v.max_multiplication_cycles))

	out = {
		"plan": dv["plan"], "variety": dv["variety"], "farm": dv["farm"],
		"version": v.name, "steps": steps, "mothers_required": mothers,
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
	"""How to find the propagation plan for a production plan.

	By variety and season, because that is how it is keyed: propagation is one pool of
	motherstock and one TC order for a crop in a year, whichever production plan it was
	last built from. Looking it up by production_plan reported no propagation plan for a
	crop that had one, as soon as a second plan was cut for the same season.
	"""
	row = frappe.db.get_value("Summer Flower Production Plan", plan,
	                          ["variety", "season_start_year"], as_dict=True)
	if not row:
		return {"production_plan": plan, "status": ["!=", "Rejected"]}
	return {"variety": row.variety,
	        "season_start_year": cint(row.season_start_year),
	        "status": ["!=", "Rejected"]}


def sizing_peak(p):
	"""The sticking week the TC order is sized from, and what it counts.

	The planned peak, which includes plantings that have no block yet, falling back
	to the placed one. The order goes to the lab months before anyone knows which
	block will be free, so sizing it to the plantings that already have one
	guarantees the plan can never be met even after the blocks are sorted out --
	and where nothing is placed at all it sized the order at zero, which is what
	made the whole TC panel read 0 plantlets against a real 3.7 million stem demand.
	"""
	planned = cint(p.get("peak_weekly_sticking_planned"))
	placed = cint(p.get("peak_weekly_sticking"))
	if planned:
		return (planned,
		        p.get("peak_sticking_week_planned") or p.get("peak_sticking_week"),
		        "planned")
	return placed, p.get("peak_sticking_week"), "placed"


def _first_sticking_for(plan, placed_only=False):
	"""Earliest sticking week the plan proposes, as a date.

	Every proposal by default, not only the placed ones. A planting with no block
	still has a sticking week, and it is the earliest one that dates the TC order;
	excluding them returned nothing at all for a plan where none are placed, so the
	order date read "not sized yet" when it was in fact already overdue.
	"""
	extra = "and ifnull(not_placed, 0) = 0" if placed_only else ""
	row = frappe.db.sql("""select sticking_year y, sticking_week w
		from `tabSummer Flower Plan Block`
		where parent = %s and is_new_planting = 1 {0}
		  and ifnull(sticking_year, 0) > 0
		order by sticking_year asc, sticking_week asc limit 1""".format(extra),
		(plan,), as_dict=True)
	return iso_monday(row[0].y, row[0].w) if row else None



# ---------------------------------------------------------------------------
# What a TC quantity actually produces
# ---------------------------------------------------------------------------

def _tc_production(plan_doc, version, tc_qty, order_date, to_prop_pct=0,
                   max_bench_sqm=None, week_overrides=None):
	"""Roll a TC quantity all the way through to stems against demand.

	The chain has always been TC -> pool -> cuttings -> plants stuck -> plantings
	-> flushes -> stems, but nothing walked it: a smaller order changed a
	percentage on a tile and left production untouched. Here the cuttings a pool
	can actually cut in a given week are what limit the plantings whose sticking
	week that is, and only the plants that get stuck are folded into the weekly
	grid. Buy less and the plantings shrink, so the stems shrink, so the coverage
	shrinks.

	Uses the plan's own flush arithmetic -- flush_offsets against the planting
	date -- so with cuttings unlimited this reproduces the plan's stored numbers
	rather than a second opinion about them.
	"""
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
	# The horizon is measured from the ORDER date to the last week the plan needs a
	# cutting, not the plan's own week count: the order goes in more than a year
	# before the plan starts, so counting weeks_covered from it stopped the run
	# short and the last sticking weeks had no capacity row at all -- which read as
	# plantings that no order size could supply.
	p = ls.params_from_version(v.name)
	# Overrides arrive keyed by the week a grower reads -- "2026-W06" -- and the
	# simulation counts weeks from the order date, so they are translated here
	# rather than making the caller do sim-week arithmetic.
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
	sim = ls.simulate(p, cint(tc_qty), order_date,
	                  farm_overrides=overrides,
	                  default_to_prop_pct=flt(to_prop_pct),
	                  max_bench_sqm=max_bench_sqm,
	                  horizon_weeks=span)
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
		# touch it. Taken from the plan's own stored contribution.
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
	# sticking week is before that cannot be supplied by it at any size, which is a
	# different answer from "buy more" and has to read differently.
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
	for b in rows:
		key = (cint(b.sticking_year), cint(b.sticking_week))
		want_plants = cint(b.plants)
		plants_wanted += want_plants
		sr = stick_rows.setdefault(key, {
			"label": "%s-W%02d" % key,
			"week_start_date": str(iso_monday(*key)),
			"capacity": total_cap_at.get(key, 0),
			"base_capacity": base_cap_at.get(key, 0),
			# Capacity that exists this week only because cuttings were diverted
			# earlier and have finished establishing.
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
				dropped += 1
				detail.append({"stick": "%s-W%02d" % key, "block": b.block,
				               "wanted": want_plants, "got": 0, "state": "dropped"})
				continue
			trimmed += 1
			state = "trimmed"
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
		"overrides": len(overrides),
		"prop_returns": sim.get("prop_pool_rows", []),
		"establishment_weeks": cint(p.get("ms_establishment_weeks")),
		"tc_to_first_cut_weeks": cint(p.get("tc_to_first_cut_weeks")),
		"ramp_weeks": len(p.get("ramp") or []),
	}


def _solve_tc_for_demand(plan_doc, version, order_date, target_pct=100.0,
                         tolerance_pct=10.0, ceiling=None):
	"""Smallest TC order whose production lands within tolerance of demand.

	Coverage rises with the order size but not smoothly -- a planting is whole beds
	and either happens or does not -- so this bisects rather than inverting a
	formula, and reports the coverage it actually reached instead of assuming the
	target was hit.
	"""
	lo, hi = 0, cint(ceiling) or 200_000
	floor_pct = target_pct - flt(tolerance_pct)
	best = None
	# Is the ceiling even enough? If not, say so rather than returning it silently.
	top = _tc_production(plan_doc, version, hi, order_date)
	if not top:
		return None
	if top["coverage_pct"] < floor_pct:
		top["solved"] = False
		top["reason"] = "even %s plantlets only reaches %.1f%%" % (
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


@frappe.whitelist()
def tc_purchase(plan=None, variety=None, farm=None, tc_qty=None, tolerance_pct=10,
                order_date=None, cycles=None):
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

	peak_plants, peak_week, peak_basis = sizing_peak(p)
	cuttings = v.cuttings_for_plants(peak_plants) if peak_plants else 0
	per_week = flt(v.cuttings_per_plant_per_week) or 1.0
	mothers = int(math.ceil(cuttings / per_week)) if cuttings else 0
	# The three levers, each falling back to what the plan is running on. Asking for
	# one and not the others has to leave the others where they were, or a tweak to
	# the quantity would silently reset a date the user had already chosen.
	cycles = cint(cycles) if cycles not in (None, "") else (
		cint(p.tc_cycles_committed) if p.tc_choice_committed
		else cint(v.max_multiplication_cycles))
	recommended = int(math.ceil(v.tc_plants_for(mothers, cycles))) if mothers else 0

	# The propagation plan nets off standing motherstock, so where one exists its
	# order is the one to place. The recommendation above is what the plan would
	# need with no motherstock at all, which is the number to sanity-check against.
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
	# from the propagation plan where one exists rather than being invented.
	if order_date:
		order_date = getdate(order_date)
	elif p.tc_choice_committed and p.tc_order_date_committed:
		order_date = getdate(p.tc_order_date_committed)
	elif prop and prop.tc_order_date and cycles == cint(v.max_multiplication_cycles):
		order_date = getdate(prop.tc_order_date)
	else:
		order_date = tc_order_by_date(v, _first_sticking_for(plan), cycles=cycles)

	# What this quantity actually grows, and the smallest quantity that lands the
	# demand. Buying less is now a production number, not a percentage on a tile.
	built = solved = None
	if order_date:
		built = _tc_production(p, v, chosen, order_date)
		solved = _solve_tc_for_demand(p, v, order_date, tolerance_pct=tol,
		                              ceiling=max(recommended * 40, 200_000))
	# When no order size lands the demand, the reader needs to know whether buying
	# more would help. Usually it would not: a planting with no block does not happen
	# however many plantlets arrive, and quoting a bigger TC number against that reads
	# as though the shortfall were a purchasing decision.
	binding = None
	if solved and not solved.get("solved"):
		if cint(p.plantings_not_placed):
			binding = _(
				"Buying more plantlets cannot close this. {0} of the {1} proposed "
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
				"than in the plantlets: the earliest sticking weeks are too close to "
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
		# leaving the reader to wonder why it is bigger than the placed plantings.
		"peak_basis": peak_basis,
		"peak_plants_placed": cint(p.peak_weekly_sticking),
		"peak_cuttings": cuttings,
		"mothers_for_peak": mothers,
		"build_up_cycles": cycles,
		# What the plan is actually committed to, so the dashboard can show a tried
		# figure as tried rather than as decided.
		# What the sourcing says, as against what the ground would grow. The plan
		# sizes plantings from demand and reports what they yield; the propagation
		# plan says whether there are cuttings to stick them with, and the two are
		# different questions with different answers. The plan cannot ask the
		# propagation plan for this while it is being built without the two becoming
		# circular, so it is carried here instead of folded into coverage.
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
                to_prop_pct=0, order_date=None, cycles=None):
	"""Try a different order, a different order date, or a different build-up.

	Same walk as tc_purchase, but it also hands back the per-sticking-week rows the
	numbers came from so they can be changed one week at a time. Nothing is saved:
	the plan on file is untouched until the choice is confirmed, so this is the
	place to find out whether the plan is worth going on with.

	Three levers, because those are the three the farm actually pulls. The quantity
	decides how much can be cut; the order date decides which weeks can be supplied
	at all; the cycles decide both how many plantlets are needed for a given pool
	and how long the lead time is, so changing them moves the order date too unless
	one is pinned. A quantity tried against the wrong date reads as a shortfall
	that buying more would not fix.
	"""
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

	cycles = cint(cycles) if cycles not in (None, "") else cint(v.max_multiplication_cycles)

	# An asked-for date wins; otherwise the propagation plan's, otherwise the one the
	# cycles imply. Asking for cycles without a date has to re-derive the date, since
	# a shorter build-up is precisely a shorter lead time.
	asked_date = getdate(order_date) if order_date else None
	if asked_date:
		order_date = asked_date
	elif prop and prop.tc_order_date and cycles == cint(v.max_multiplication_cycles):
		order_date = prop.tc_order_date
	else:
		order_date = tc_order_by_date(v, _first_sticking_for(plan), cycles=cycles)
	if not order_date:
		return {"plan": plan, "error": "No sticking weeks to plan cuttings for."}

	# What to buy for the cycles being ASKED for, which is the whole point of the
	# lever: the pool is the same size either way, so twice the build-up rounds is
	# half the plantlets. Sized off the pool the propagation plan worked out, by the
	# same helper the motherstock batch uses, so the recommendation and the batch
	# cannot disagree.
	same_cycles = cycles == cint(v.max_multiplication_cycles)
	if prop and cint(prop.mother_plants_required):
		recommended = int(math.ceil(
			v.tc_plants_for(cint(prop.mother_plants_required), cycles)))
	elif prop and same_cycles:
		recommended = cint(prop.tc_plants_required)
	else:
		peak = v.cuttings_for_plants(sizing_peak(p)[0])
		per_week = flt(v.cuttings_per_plant_per_week) or 1.0
		factor = v.multiplication_factor(cycles)
		recommended = int(math.ceil(peak / per_week / factor)) if per_week and factor else 0

	# A typed quantity wins. Otherwise take the plan's own figure only while the
	# cycles are the plan's own -- reusing it under different cycles is what made
	# the quantity sit still while the order date moved, so the order on screen
	# raised a pool that no longer matched the one being asked for.
	if cint(tc_qty):
		chosen = cint(tc_qty)
	elif prop and same_cycles and cint(prop.tc_plants_required):
		chosen = cint(prop.tc_plants_required)
	else:
		chosen = recommended

	built = _tc_production(p, v, chosen, order_date, to_prop_pct=flt(to_prop_pct),
	                       week_overrides=week_overrides)
	# The plan as it stands, for comparison: what changing anything is measured
	# against. Read from the document, not recomputed, so the baseline is the plan.
	return {
		"plan": plan, "variety": p.variety, "farm": p.farm, "version": v.name,
		"status": p.workflow_state or p.status,
		"order_date": str(order_date),
		"tc_qty": chosen,
		"cycles": cycles,
		# For the cycles asked for, not for the plan's. What the plan is running on
		# is in "current" below, which is what this used to duplicate.
		"recommended_tc": cint(recommended),
		"propagation_plan": prop.name if prop else None,
		# What the plan is running on now, so the three levers can be shown as
		# changed-from rather than as bare numbers.
		"current": {
			"tc_qty": cint(prop.tc_plants_required) if prop else 0,
			"order_date": str(prop.tc_order_date) if prop and prop.tc_order_date else None,
			"cycles": cint(v.max_multiplication_cycles),
			"lead_time_weeks": cint(v.lead_time_for_cycles(cint(v.max_multiplication_cycles))),
		},
		"lead_time_weeks": cint(v.lead_time_for_cycles(cycles)),
		"multiplication_factor": flt(v.multiplication_factor(cycles)),
		"mother_plants_from_order": int(round(v.mother_plants_for(chosen, cycles))),
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
			"net_area_ha": flt(b.net_area_ha),
			"lifetime_stems": b.lifetime_stems or 0,
			"below_minimum": bool(b.below_minimum),
			"in_past": bool(b.planting_in_past),
			"unallocated": bool(b.is_new_planting and not b.block),
			"not_placed": bool(b.get("not_placed")),
			"notes": b.notes,
			# The child row's own name, so the allocation control can assign a block
			# to this exact planting rather than matching on index and dates.
			"row": b.name,
			"is_new": bool(b.is_new_planting),
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
		# A submitted plan's blocks are not editable, so the allocation control is not
		# offered on one.
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
			"cuttings": sum(x["cuttings"] for x in new_rows),
			"blocks_available": blocks_available,
			"blocks_needed": len(new_rows),
			"weeks_in_ground": life_weeks,
			"min_planting_beds": v.min_planting_beds_derived or 0,
		},
	}


def _sticking_demand(plan, version):
	"""Cuttings the field wants each week, from the plan's own sticking schedule.

	This is what makes the weekly split a decision instead of a habit. Sending
	everything to the field is only right while the field can use it all; in a week
	the field wants less than the pool cuts, the surplus is either wasted or put
	back into propagation, where it returns as motherstock in time for a peak that
	has not arrived yet. That is how a later demand is met without buying more
	plantlets for it.
	"""
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

	# Two different things are called cycles here and they must not be confused.
	# num_cycles is generations: how many times the whole TC -> motherstock -> expiry
	# loop runs over the horizon. build_up_cycles is multiplication: how many rounds
	# one order is grown through before it becomes mother plants. Only the second
	# changes what an order is worth, and it pays for the extra plants in lead time,
	# so both have to move together or the simulation promises a pool weeks before
	# it could exist.
	if build_up_cycles not in (None, ""):
		v = frappe.get_cached_doc("Crop Protocol Version", version)
		c = cint(build_up_cycles)
		p["multiplication_factor"] = flt(v.multiplication_factor(c))
		p["build_up_cycles"] = c
		# NOT lead_time_for_cycles: that is the time to the full pool, which is one
		# establishment per cycle. The first cutting comes at the end of the first
		# establishment however many cycles follow it, and setting it from the full
		# figure pushed the first cut out by a whole build-up -- week 36 for a
		# two-cycle order whose plantlets are cuttable at 18.
		p["tc_to_first_cut_weeks"] = cint(v.weeks_tc_to_first_cut())

	# What the field asks for, week by week, keyed to the simulation's own clock so
	# the split can be judged against it rather than guessed at.
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
		fields=["name", "block", "farm", "custom_total_beds", "custom_net_area_ha"],
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
				# 4,789 beds in Karen's blocks are each 50 m² with no length or width,
				# so multiplying reported nought square metres of 24.149 hectares.
				"area_sqm": (flt(r.bed_length) * flt(r.bed_width)) or flt(r.bed_area),
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
		# Bed status is the better answer where it is maintained, because a bed pulled
		# early frees up the moment its status changes. It is not maintained here:
		# creating a Planting Calendar does not touch the beds, so 14 blocks held
		# plantings while 4 bed records out of 4,789 said Planted and the farm read as
		# 0.1% used. Where a block has plantings but no bed says so, the plantings are
		# the authority and the basis is reported so nobody reads a bed count that was
		# never kept.
		held = sum(cint(r.get("beds")) for r in rows
		           if r.get("calendar_status") not in ("Uprooted", "Cancelled"))
		basis = "bed status"
		if held and not used:
			basis = "plantings"
			held = min(held, len(beds) or held)
			# Area follows the same basis, or the bar would say 300 beds used and
			# nought square metres of them.
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
				# bed register from a count inferred off the plantings.
				"basis": basis,
				"pct_used": round(
					(held if basis == "plantings" else len(used))
					* 100.0 / len(beds), 2) if beds else 0.0,
				"net_sqm_total": round(net_total, 1),
				"net_sqm_used": round(net_used, 1),
				"net_sqm_free": round(net_total - net_used, 1),
				"beds_measured": measured,
				"area_complete": bool(beds) and measured == len(beds),
				"plants_standing": (sum(cint(r.get("plants")) for r in rows
				                        if r.get("calendar_status")
				                        not in ("Uprooted", "Cancelled"))
				                    if basis == "plantings"
				                    else sum(x["plants"] for x in used)),
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
	# round and so the whole schedule hanging off it, which makes them among the
	# most consequential settings here -- and they were not editable at all.
	"establishment_includes_hardening", "establishment_includes_ramp",
	"cycle_time_weeks", "motherstock_life_weeks", "rooting_success_pct",
	"field_establishment_pct", "cutting_reject_pct", "stated_yield_stems_per_ha",
	"ready_cutting_price", "climate_note",
)


@frappe.whitelist()
def lifetime_production(variety=None, farm=None, plan=None, grain="month"):
	"""Every stem a standing planting will yield, to the end of its life.

	The block forecast stops at the plan's horizon, because that is the question a plan
	asks. A planting outlives it: Aster is 111 weeks in the ground and a plan covers 52,
	so more than half of what is already planted falls outside every chart on this
	dashboard. This is the rest of it -- what the ground will produce whether or not
	anyone plans for it -- flush by flush, from the plantings that exist.
	"""
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
	# calendar read, so nothing is recomputed here.
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
			# the planting's own total is visible rather than silently trusted.
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


@frappe.whitelist()
def protocol_detail(version=None, variety=None, farm=None, plan=None):
	"""The whole protocol sheet: inputs, derived values and the journey timelines.

	Three ways to arrive at a version, in order of how specific they are.

	A version named outright wins -- that is the picker on the tab. Otherwise a plan
	in scope names its own, because the plan is built on a pinned version and the rest
	of the dashboard is describing that plan: showing the version in force beside it
	would put a different set of numbers on the same screen, and a plan sits on a
	superseded version routinely.

	Only with neither does it fall back to the version in force for a variety. That
	fallback used to be the only path -- the dashboard passed plan= and this had no
	such argument, so Frappe dropped it -- which is why the Protocol tab stayed on
	Aster Pink Flash while every other tab followed the plan you had picked.
	"""
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
	plants_per_ha = (v.plants_per_sqm_net or 0) * 10_000
	for r in sorted(v.flush_schedule, key=lambda r: r.flush_number or 0):
		cum += r.stems_per_plant or 0
		flushes.append({
			"flush": r.flush_number,
			"weeks_from_pinch": r.weeks_from_pinch,
			"weeks_from_planting": (v.weeks_to_pinch or 0) + (r.weeks_from_pinch or 0),
			# Kept for callers that still read it; there is no grid allowance now, so
			# it is the same figure. Aster reads 20 here, not 21.
			"weeks_from_planting_gridded": (v.weeks_to_pinch or 0)
			+ (r.weeks_from_pinch or 0),
			"stems_per_plant": flt(r.stems_per_plant),
			"stems_per_ha": int(round(flt(r.stems_per_plant) * plants_per_ha)),
			"cumulative_stems_per_plant": round(cum, 2),
		})

	# TC order through to the first harvest off those cuttings.
	#
	# Built from lead_time_weeks, which is the same figure the plan's order-by date
	# works back from, so the two cannot disagree. They did: this card walked order ->
	# establishment -> cut and reached 81 weeks, while the plan assumed 103 -- because
	# the card ignored the multiplication cycles the order's SIZE depends on. You
	# cannot order a fifth of the mother plants and also cut eighteen weeks after they
	# land. It counted hardening twice as well, once inside establishment and again on
	# the way to the field.
	lead = cint(v.supplier_lead_weeks)
	estab = cint(v.ms_establishment_weeks)
	cycles = cint(v.max_multiplication_cycles)
	build = cycles * cint(v.cycle_time_weeks)
	to_cut = cint(v.lead_time_weeks)          # arrival to the first cutting
	stick_to_plant = cint(v.sticking_to_planting_weeks)
	plant_to_harvest = cint(v.first_harvest_offset_weeks)

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
		# is showing the plan's pinned protocol or the one in force -- they differ
		# routinely, and a reader who cannot tell which is which cannot trust either.
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

	# Silently dropping an edit is worse than refusing it: the save reports success,
	# the figure does not move, and the only clue is that nothing happened.
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
		                      filters=_prop_filters(plan), pluck="name",
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


@frappe.whitelist()
def confirm_tc_choice(plan, tc_qty=None, order_date=None, cycles=None, reason=None):
	"""Commit a tried quantity, order date or build-up, and carry it downstream.

	plan_whatif answers "what would happen"; this is the answer to "do it". The
	three documents that each hold a piece of the same decision are written in one
	go, because leaving any of them behind is how the plan, the propagation plan
	and the batch came to disagree about the same order in the first place:

	    production plan   what is being bought and what it supplies
	    propagation plan  the requirement it was raised against
	    motherstock batch the order itself, and the schedule hanging off it

	The batch takes the figures as overrides rather than as its own calculation, so
	it keeps saying what the requirement was and what was chosen instead. Confirming
	the calculated figure clears the override rather than pinning it, so a confirm
	that changes nothing leaves nothing behind to go stale.

	A submitted plan is not touched: the numbers on it have been approved, and the
	way to change those is to amend the plan, not to have an endpoint quietly
	rewrite them underneath the approval.
	"""
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
	# a shorter lead time and therefore a later order date -- that is most of why
	# anyone cuts cycles. Keeping the old date while changing the cycles would
	# confirm a schedule the lead time contradicts, so where no date was asked for
	# and the cycles have moved, the date is re-derived rather than carried over.
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
	# season, which is right for reading -- a season has one propagation plan -- but
	# wrong for writing: confirming on one plan would rewrite the propagation plan
	# belonging to another plan for the same variety and season, which is exactly
	# what an amended plan and its original are.
	prop = frappe.get_all("Summer Flower Propagation Plan",
	                      filters={"production_plan": plan, "docstatus": ["<", 2]},
	                      fields=["name", "docstatus"], order_by="creation desc", limit=1)
	# and no fallback. Matching on variety and season would find the propagation
	# plan of a different plan for the same season -- an amended plan and its
	# original are exactly that -- and write this plan's order onto it. A plan with
	# no propagation plan of its own has nothing to carry the choice to yet; one is
	# raised when the plan is approved.
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
		changed.append(_("no propagation plan is raised for this plan yet, so there "
		                 "was nothing to carry it to"))

	# ---- the motherstock batches raised for this plan
	for b in frappe.get_all("Summer Flower Motherstock Batch",
	                        filters={"production_plan": plan, "docstatus": 0},
	                        fields=["name"]):
		bd = frappe.get_doc("Summer Flower Motherstock Batch", b.name)
		bd.build_up_cycles = cycles
		# Confirming what the maths already says should clear the override, not pin
		# it: a batch marked as overridden when it is not would stop following the
		# protocol the next time the protocol moves.
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

	note = _("Confirmed {0} plantlets, {1} cycles, ordering {2}. That order supplies "
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
	"""The plan as one row per planting and one column per week.

	Everything else on this dashboard reads the plan as a total: so many stems in
	so many weeks. This is the plan the way it is worked -- which planting, in which
	block, puts which stems in which week -- and it is the shape the farm's own
	workbook uses, so a plan can be checked against the sheet it replaces.

	Nothing here recalculates a stem. It arranges what _populate already recorded
	while it folded the plantings in, which is why the columns add up to the plan's
	own weekly figures rather than to a second opinion about them.
	"""
	_guard()
	plan = resolve_plan(variety, farm, plan)
	if not plan:
		return {"plan": None}
	from upande_summer_flowers.summer_flowers import plan_sheet

	return plan_sheet.build(plan)


# The CSV is plan_sheet.sheet_csv, which is whitelisted and writes the download
# response itself. Wrapping it here would be a second implementation of the one
# thing this file keeps having to un-duplicate.
