# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""How many plantlets to buy, and how long to divert the cut before planting.

The farm's own description of what happens, which is what this solves against:

    A mother cuts every week of its productive life. So the second generation
    does not wait for the first to finish -- week one's cuttings start one batch
    of new mothers, week two's start another, and each is one establishment away
    from producing, a week apart rather than a generation apart. While that is
    happening the first mothers are still cutting, so from the week you decide
    you have enough coming, each week's cut splits: some back as mothers, the
    rest rooted and sent to the field. And the whole block is cleared together,
    so a late generation dies with the first one rather than living on.

That leaves exactly two things to decide, and they trade against each other:

    * how many TC plantlets to buy
    * how many weeks to send the WHOLE cut back before any goes to the field

Buy fewer and you divert longer, which means ordering earlier. Everything else
-- the order date, when the first plants land, when the pool peaks, whether the
weekly stream actually covers the weekly planting need -- follows from those two
and the protocol.

This deliberately does not use multiplication_factor or lead_time_for_cycles.
Those model the build-up as N sequential establishments, which is the thing the
farm says does not happen; a four-cycle protocol came out at 60 weeks to a full
pool against a motherstock life of 52, so the plan was working the TC order back
from a pool that lands after the block is cleared.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers import lifecycle_sim as ls
from upande_summer_flowers.summer_flowers.planning import iso_monday

# A plantlet buys one mother. The build-up is the diverted cuttings below, not a
# factor applied to the order -- counting both is how one order came to look like
# four tranches of mothers that nobody ever cut for.
TC_IS_ONE_MOTHER = {"multiplication_factor": 1.0, "build_up_cycles": 1}

MAX_TC = 2_000_000
SEARCH_CEILING_STEPS = 24


def demand_by_sticking_week(plan):
	"""{Monday of the sticking week: cuttings the propagation unit must deliver}

	Keyed on sticking, not planting: the cutting has to come off the mother the
	week it is stuck, and the weeks between sticking and planting are the rooting
	the propagation unit does. Grossed up by cuttings_per_plant_required, so a
	rooting loss is bought rather than discovered.
	"""
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	per_plant = flt(v.cuttings_per_plant_required) or 1.0
	out = {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not cint(b.sticking_year):
			continue
		monday = iso_monday(cint(b.sticking_year), cint(b.sticking_week))
		out[monday] = out.get(monday, 0) + int(round(cint(b.plants) * per_plant))
	return dict(sorted(out.items()))


def last_useful_divert_weeks(p):
	"""Diverting after this many weeks of cutting buys mothers that never cut.

	A cutting stuck in week w is productive at w + establishment, and the block is
	cleared one life after its first cut. So the deadline is a real date, not a
	bench size.
	"""
	estab = cint(p.get("cutting_to_mother_weeks")) or cint(p["ms_establishment_weeks"])
	return max(0, cint(p["ms_life_weeks"]) - estab)


def order_date_for(p, first_sticking, divert_weeks):
	"""Back from the week the field's first cuttings must be stuck.

	first cut = order + supplier lead + establishment; the first `divert_weeks` of
	cutting go back as mothers; the week after that is the field's first sticking.
	"""
	weeks = (cint(p["supplier_lead_weeks"])
	         + (cint(p.get("tc_to_first_cut_weeks")) or cint(p["ms_establishment_weeks"]))
	         + cint(divert_weeks))
	return add_days(getdate(first_sticking), -7 * weeks)


def evaluate(version, tc, divert_weeks, demand, first_sticking, tc_loss_pct=0.0):
	"""Run one candidate and say whether it covers every planting week."""
	p = ls.params_from_version(version, TC_IS_ONE_MOTHER)
	order = order_date_for(p, first_sticking, divert_weeks)
	arriving = int(round(flt(tc) * (1 - flt(tc_loss_pct) / 100)))
	if arriving <= 0:
		return None

	def sw_of(d):
		return (getdate(d) - getdate(order)).days // 7

	demand_by_sw = {sw_of(d): n for d, n in demand.items() if sw_of(d) >= 0}
	if not demand_by_sw:
		return None
	last_sw = max(demand_by_sw)

	first_cut_sw = (cint(p["supplier_lead_weeks"])
	                + (cint(p.get("tc_to_first_cut_weeks"))
	                   or cint(p["ms_establishment_weeks"])))
	# Everything cut in the diversion window goes back, whatever the field asked
	# for. After it, simulate serves the demand and banks the surplus.
	overrides = {first_cut_sw + i: 0 for i in range(cint(divert_weeks))}

	sim = ls.simulate(p, arriving, order, num_cycles=1, farm_overrides=overrides,
	                  demand_by_sw=demand_by_sw, horizon_weeks=last_sw + 8,
	                  # Multiplying ends when the window does. After it the cut goes
	                  # to the field, and a surplus the field has not asked for stays
	                  # on the mother instead of being banked as yet more mothers.
	                  divert_until_sw=first_cut_sw + cint(divert_weeks))
	rows = {r["sw"]: r for r in sim["rows"]}

	weeks, short_weeks, delivered, shortfall = 0, 0, 0, 0
	for sw, need in sorted(demand_by_sw.items()):
		weeks += 1
		got = cint((rows.get(sw) or {}).get("to_farm"))
		delivered += min(got, need)
		if got < need:
			short_weeks += 1
			shortfall += need - got
	cap = [cint(r.get("total_cap") or 0) for r in sim["rows"]]
	line_end = sim["cycles"][0]["line_end_sw"] if sim.get("cycles") else None

	return {
		"tc": cint(tc), "arriving": arriving, "divert_weeks": cint(divert_weeks),
		"order_by": order, "covers": short_weeks == 0,
		"weeks": weeks, "weeks_met": weeks - short_weeks, "short_weeks": short_weeks,
		"shortfall": shortfall, "delivered": delivered,
		"peak_pool": max(cap) if cap else 0,
		"peak_pool_week": cap.index(max(cap)) if cap else None,
		"first_cut_sw": first_cut_sw,
		"first_cut_date": add_days(order, 7 * first_cut_sw),
		"arrive_date": add_days(order, 7 * cint(p["supplier_lead_weeks"])),
		"line_end_sw": line_end,
		"line_end_date": add_days(order, 7 * line_end) if line_end else None,
		"total_to_field": sum(cint(r.get("to_farm") or 0) for r in sim["rows"]),
		"wasted": sum(cint(r.get("cuttings_wasted") or 0) for r in sim["rows"]),
		"not_taken": sum(cint(r.get("cuttings_not_taken") or 0) for r in sim["rows"]),
		"sim": sim,
	}


def least_tc_for(version, divert_weeks, demand, first_sticking, tc_loss_pct=0.0):
	"""Smallest order that covers every planting week at this diversion.

	Cover only ever improves with more plantlets, so this is a bisection rather
	than a sweep -- but only once the diversion is possible at all. Diverting
	longer pulls the order earlier, which pulls the block's clearance earlier too,
	and past a point the block is cleared before the season's last planting. No
	quantity of plantlets fixes that, so it is reported rather than searched.
	"""
	probe = evaluate(version, max(1, sum(demand.values()) // 100), divert_weeks,
	                 demand, first_sticking, tc_loss_pct)
	if not probe:
		return None
	last_sticking = max(demand)
	if probe["line_end_date"] and getdate(probe["line_end_date"]) <= getdate(last_sticking):
		return {
			"tc": None, "divert_weeks": cint(divert_weeks),
			"order_by": probe["order_by"], "covers": False,
			"line_end_date": probe["line_end_date"],
			# Not "before": the clearance falling ON the last planting week is just
			# as fatal, and saying "before 2028-12-11" of 2028-12-11 reads as a bug.
			"blocked": _("Diverting {0} weeks pulls the order {0} weeks earlier, so "
			             "the block would be cleared on {1}. The season's last "
			             "planting is {2}, and the block has to outlast it. No "
			             "quantity of plantlets changes that.")
			.format(cint(divert_weeks), probe["line_end_date"], last_sticking),
		}

	total = sum(demand.values()) or 1
	lo, hi = 1, 0
	step = max(1, total // 100)
	for _i in range(SEARCH_CEILING_STEPS):
		step *= 2
		if step > MAX_TC:
			break
		r = evaluate(version, step, divert_weeks, demand, first_sticking, tc_loss_pct)
		if r and r["covers"]:
			hi = step
			break
	if not hi:
		return {
			"tc": None, "divert_weeks": cint(divert_weeks),
			"order_by": probe["order_by"], "covers": False,
			"blocked": _("Even {0} plantlets do not cover every planting week at "
			             "{1} weeks of diversion.").format(f"{MAX_TC:,}",
			                                               cint(divert_weeks)),
		}
	while lo < hi:
		mid = (lo + hi) // 2
		r = evaluate(version, mid, divert_weeks, demand, first_sticking, tc_loss_pct)
		if r and r["covers"]:
			hi = mid
		else:
			lo = mid + 1
	return evaluate(version, lo, divert_weeks, demand, first_sticking, tc_loss_pct)


def _divert_candidates(p):
	"""Diversion lengths worth trying, coarse then fine near the useful end."""
	last = last_useful_divert_weeks(p)
	steps = sorted({0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 26, 32, 40, 52})
	return [d for d in steps if d <= last] or [0]


def recommend(plan, tc=None, divert_weeks=None):
	"""The whole answer the dialog draws: one recommendation, and what it means.

	The recommendation is the SMALLEST order that covers every planting week and
	can still be placed, because plantlets are what this costs. Diverting longer
	buys the same cover for fewer plantlets, but each extra week of diversion
	pulls the order date a week earlier, and an order date in the past is not an
	option however cheap it looks.
	"""
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
			lookup_tc_rate,
		)

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	if not entry or not sourcing.standing_stage(v, entry):
		return {"propagates": False, "reason": decided.get("reason"),
		        "protocol": v.name, "variety": plan.variety, "farm": plan.farm}

	demand = demand_by_sticking_week(plan)
	if not demand:
		return {"propagates": True, "solvable": False,
		        "reason": _("This plan has no new plantings, so there is nothing "
		                    "to propagate for."),
		        "protocol": v.name, "variety": plan.variety, "farm": plan.farm}

	p = ls.params_from_version(plan.protocol, TC_IS_ONE_MOTHER)
	loss = flt(v.tc_order_loss_pct)
	first_sticking = min(demand)
	today = getdate(nowdate())

	options = []
	for d in _divert_candidates(p):
		r = least_tc_for(plan.protocol, d, demand, first_sticking, loss)
		if not r:
			continue
		r.pop("sim", None)
		r["order_late"] = getdate(r["order_by"]) < today
		r["cost"] = (flt(lookup_tc_rate(r["tc"])) * r["tc"]) if r.get("tc") else None
		options.append(r)

	# A blocked option is shown, not hidden: "you cannot divert that long, and
	# here is why" is the answer to the question somebody was about to ask.
	usable = [o for o in options if o.get("covers")]
	placeable = [o for o in usable if not o["order_late"]]
	# Cheapest that can still be ordered. Nothing placeable means the decision is
	# late, not impossible, and the dialog has to say which.
	pick = (min(placeable, key=lambda o: (o["tc"], -o["divert_weeks"]))
	        if placeable else
	        (min(usable, key=lambda o: (o["tc"], -o["divert_weeks"]))
	         if usable else None))

	chosen_d = (cint(divert_weeks) if divert_weeks not in (None, "")
	            else (pick or {}).get("divert_weeks", 0))
	if tc not in (None, "", 0):
		chosen_tc = cint(tc)
	else:
		# The least that covers AT THIS DIVERSION, not the recommendation's own
		# quantity. Moving the weeks and keeping the other number is how the panel
		# came to report a shortfall against a quantity nobody had chosen.
		at_d = next((o for o in usable if o["divert_weeks"] == chosen_d), None)
		if not at_d:
			r = least_tc_for(plan.protocol, chosen_d, demand, first_sticking, loss)
			at_d = r if (r and r.get("covers")) else None
		chosen_tc = (at_d or pick or {}).get("tc")
	detail = (evaluate(plan.protocol, chosen_tc, chosen_d, demand, first_sticking,
	                   loss) if chosen_tc else None)
	if detail:
		detail.pop("sim", None)
		detail["order_late"] = getdate(detail["order_by"]) < today
		detail["cost"] = flt(lookup_tc_rate(detail["tc"])) * detail["tc"]

	standing = sourcing.standing_pool_capacity(v, first_sticking)
	last_divert = last_useful_divert_weeks(p)

	return {
		"propagates": True, "solvable": bool(usable),
		"protocol": v.name, "variety": plan.variety, "farm": plan.farm,
		"plan": plan.name, "entry_stage": entry,
		# What the plan asks the propagation unit for.
		"need": {
			"plants": sum(cint(b.plants) for b in plan.plan_blocks
			              if cint(b.is_new_planting)),
			"cuttings": sum(demand.values()),
			"weeks": len(demand),
			"first_sticking": str(first_sticking),
			"last_sticking": str(max(demand)),
			"peak_week_cuttings": max(demand.values()),
			"by_week": [{"date": str(d), "cuttings": n} for d, n in demand.items()],
			"cuttings_per_plant": flt(v.cuttings_per_plant_required) or 1.0,
		},
		"recommended": pick,
		"chosen": detail,
		"options": options,
		"limits": {
			# What the protocol allows, and what THIS season allows. The second is
			# usually the binding one, and quoting only the first told a reader they
			# had 41 weeks to play with when the answer was 4.
			"last_divert_weeks": last_divert,
			"max_divert_that_covers": (max(o["divert_weeks"] for o in usable)
			                           if usable else None),
			"blocked_above": (min((o["divert_weeks"] for o in options
			                       if not o.get("covers")), default=None)),
			"last_divert_date": str(add_days(
				first_sticking, 7 * (last_divert - (cint(divert_weeks) or 0)))),
			"life_weeks": cint(p["ms_life_weeks"]),
			"establishment_weeks": cint(p.get("tc_to_first_cut_weeks"))
			or cint(p["ms_establishment_weeks"]),
			"cutting_establishment_weeks": cint(p.get("cutting_to_mother_weeks")),
			"supplier_lead_weeks": cint(p["supplier_lead_weeks"]),
			"tc_loss_pct": loss,
		},
		# Shown, never netted off: the order is sized for the whole need, and what
		# is already standing is the reader's to weigh.
		"standing": {
			"plants": cint(standing),
			"as_at": str(first_sticking),
		},
		"assumed": _assumptions(v),
	}


def _assumptions(v):
	"""Protocol figures that are defaults rather than measurements.

	cuttings_per_plant_per_week drives every number here, and a 1.0 that nobody
	filled in is indistinguishable from a 1.0 an agronomist measured.
	"""
	out = []
	if flt(v.cuttings_per_plant_per_week) in (0, 1):
		out.append(_("Cuttings per mother per week is {0} on this protocol. Every "
		             "figure here scales directly with it.")
		           .format(flt(v.cuttings_per_plant_per_week) or 1))
	if not flt(v.rooting_success_pct):
		out.append(_("Rooting success is not set, so no rooting loss is allowed for."))
	if not cint(v.motherstock_life_weeks):
		out.append(_("Motherstock life is not set, so the block is never cleared."))
	if not cint(v.supplier_lead_weeks):
		out.append(_("Supplier lead is not set, so the plantlets are treated as "
		             "arriving the week they are ordered."))
	if not flt(v.tc_order_loss_pct):
		out.append(_("No TC order loss allowance, so every plantlet ordered is "
		             "assumed to survive the transfer."))
	return out


@frappe.whitelist()
def options(production_plan, tc=None, divert_weeks=None):
	"""Everything the propagation dialog draws, for one production plan."""
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	return recommend(plan, tc=tc, divert_weeks=divert_weeks)
