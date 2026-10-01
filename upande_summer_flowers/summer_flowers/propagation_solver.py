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
import math

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers import lifecycle_sim as ls
from upande_summer_flowers.summer_flowers.planning import iso_monday

# A plantlet buys one mother. The build-up is the diverted cuttings below, not a
# factor applied to the order -- counting both is how one order came to look like
# four tranches of mothers that nobody ever cut for.
TC_IS_ONE_MOTHER = {"multiplication_factor": 1.0, "build_up_cycles": 1}

#: The farm sends a motherstock block back to multiplication four times at the
#: most -- "i only send 4 times". Past that the block is simply cut for the
#: field until it is cleared.
MAX_MULTIPLICATIONS = 4

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
	# The chosen pair comes back with its whole schedule, because the week list is
	# the thing the reader is here to look at -- the quantity is a by-product.
	detail = (schedule_for(plan.protocol, chosen_tc, chosen_d, demand,
	                       first_sticking, loss) if chosen_tc else None)
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


def _generation_of(start_sw, first_cut_sw, estab):
	"""Which generation a pool coming online in `start_sw` belongs to.

	Generations are bands one establishment wide, counted from the first cut of
	the plantlets. Gen 1 is what the plantlets became. Cuttings taken off gen 1
	establish one band later and are gen 2; cuttings taken once gen 2 is cutting
	are gen 3, and so on.

	Within a band the pools arrive a week apart rather than together, because a
	mother cuts every week -- so "gen 2 arrives" is a run of weeks, not a date.
	That is the whole difference from counting multiplication cycles, and it is
	why the number of generations follows from how long the cut is diverted
	instead of being set on its own.
	"""
	if not estab or start_sw <= first_cut_sw:
		return 1
	return 1 + max(1, int(round((start_sw - first_cut_sw) / float(estab))))


def schedule_for(version, tc, divert_weeks, demand, first_sticking, tc_loss_pct=0.0):
	"""The line week by week, and the generations that make it up.

	This is what the reader actually wants to see: not a quantity, but when each
	generation starts cutting, how many mothers are standing that week, how the
	week's cut splits, and whether the field got what it asked for.
	"""
	p = ls.params_from_version(version, TC_IS_ONE_MOTHER)
	res = evaluate(version, tc, divert_weeks, demand, first_sticking, tc_loss_pct)
	if not res:
		return None
	sim = res.pop("sim")
	order = getdate(res["order_by"])
	first_cut_sw = res["first_cut_sw"]
	line_end = res["line_end_sw"] or 0
	estab = cint(p.get("cutting_to_mother_weeks")) or cint(p["ms_establishment_weeks"])

	def sw_of(d):
		return (getdate(d) - order).days // 7

	demand_by_sw = {sw_of(d): n for d, n in demand.items() if sw_of(d) >= 0}

	# ---- the generations, as bands rather than dates
	bands = {}
	arriving = res["arriving"]
	bands[1] = {"generation": 1, "mothers": arriving,
	            "first_sw": first_cut_sw, "last_sw": first_cut_sw, "pools": 1}
	for pool in sim.get("prop_pool_rows") or []:
		s = cint(pool.get("ready_sw"))
		g = _generation_of(s, first_cut_sw, estab)
		b = bands.setdefault(g, {"generation": g, "mothers": 0,
		                         "first_sw": s, "last_sw": s, "pools": 0})
		b["mothers"] += cint(pool.get("plants"))
		b["first_sw"] = min(b["first_sw"], s)
		b["last_sw"] = max(b["last_sw"], s)
		b["pools"] += 1

	generations = []
	for g in sorted(bands):
		b = bands[g]
		generations.append({
			"generation": g,
			"mothers": b["mothers"],
			"arrivals": b["pools"],
			"first_cut_date": str(add_days(order, 7 * b["first_sw"])),
			"last_arrival_date": str(add_days(order, 7 * b["last_sw"])),
			"stuck_date": str(add_days(order, 7 * max(0, b["first_sw"] - estab)))
			if g > 1 else None,
			# The block is cleared as one, so a later generation simply gets less.
			"cutting_weeks": max(0, line_end - b["first_sw"]),
			"expiry_date": str(add_days(order, 7 * line_end)) if line_end else None,
		})
	gen_start = {b["first_sw"]: g for g, b in bands.items()}

	# ---- the weeks
	rows, running = [], 0
	for r in sim["rows"]:
		sw = cint(r["sw"])
		if sw < first_cut_sw or (line_end and sw > line_end):
			continue
		to_field = cint(r.get("to_farm"))
		running += to_field
		need = cint(demand_by_sw.get(sw, 0))
		live = sorted({g for g, b in bands.items() if b["first_sw"] <= sw < line_end})
		event = ""
		if sw in gen_start and gen_start[sw] > 1:
			event = _("Gen {0} starts cutting").format(gen_start[sw])
		elif sw == first_cut_sw:
			event = _("Gen 1 first cut")
		elif line_end and sw == line_end:
			event = _("Block cleared")
		rows.append({
			"week_no": sw - first_cut_sw + 1,
			"week_start": str(add_days(order, 7 * sw)),
			"event": event,
			"generations_live": ", ".join("G%d" % g for g in live),
			"mothers_standing": cint(r.get("ms_plants")),
			"cuttings_cut": cint(r.get("total_cap")),
			"to_multiplication": cint(r.get("to_prop")),
			"to_field": to_field,
			"cumulative_to_field": running,
			"demand": need,
			"shortfall": max(0, need - to_field),
		})
	res["generations"] = generations
	res["weeks_table"] = rows
	return res


# ---------------------------------------------------------------------------
# Sizing off the peak week, with the multiplication the system works out
# ---------------------------------------------------------------------------

def round_up_to_area(plants, per_area):
	"""Up to a whole minimum planting area. Nobody buys a third of a bed."""
	if not per_area or per_area <= 0:
		return int(math.ceil(plants))
	return int(math.ceil(plants / float(per_area)) * per_area)


def peak_sizing(plan, cycles=None, max_cycles=MAX_MULTIPLICATIONS):
	"""What to buy, sized by the busiest week, and how often to multiply.

	The pool is bought once and cut from every week of the season, so the week
	that sets its size is the busiest one -- the weeks either side lend it
	nothing. Divide that week's cuttings by the generations standing (the
	plantlets plus one per multiplication) and round up to a whole planting area.

	What a multiplication costs is time, and that is the part nobody should have
	to work out. Each one is an establishment of a diverted cutting, so the pool
	is full that much later, the order goes in that much earlier, and the line --
	which still dies one motherstock life after its FIRST cut -- has that many
	fewer weeks left to cut from. So the suggestion is the most multiplication
	that still leaves the pool enough cutting weeks to see the season out.
	"""
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	demand = demand_by_sticking_week(plan)
	if not demand:
		return None
	peak = max(demand.values())
	peak_week = max(demand, key=lambda d: demand[d])
	first_stick = min(demand)
	estab = cint(v.weeks_tc_to_first_cut())
	regen = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)
	lead = cint(v.supplier_lead_weeks)
	per_week = flt(v.cuttings_per_plant_per_week) or 1.0
	per_area = cint(v.plants_per_bed) or cint(v.min_planting_area_sqm) or 1
	life = cint(v.motherstock_life_weeks)
	need_weeks = len(demand)
	# The span the line has to stay alive across, not how many weeks are in it.
	# 38 sticking weeks that run from January to December need the line alive for
	# 46 weeks, and comparing 41 cutting weeks to the count of 38 said yes to a
	# pool that dies six sticking weeks before the end.
	last_stick = max(demand)
	span_weeks = (getdate(last_stick) - getdate(first_stick)).days // 7
	# A generation ramps before it cuts at full rate, so the pool has to START
	# cutting that much before the field wants anything. Without this the first
	# cutting week and the first sticking week were the same, and the pool was at
	# 25% in a week that needed 25,000 cuttings -- 22,250 short across the ramp.
	ramp_lead = max(0, len(v.ramp_ratios() or [1.0]) - 1)

	ramp = v.ramp_ratios() or [1.0]
	options = []
	for c in range(0, cint(max_cycles) + 1):
		# Sized to the shape of the season, not to a flat division of its peak.
		# Only generation one is bought; the rest are cuttings taken off it, so
		# the plantlet order is generation one's size alone.
		gplan = generation_plan(demand, per_week, per_area, c + 1)
		gens = len(gplan) or 1
		mothers = peak / per_week
		buy = cint(gplan[0]["mothers"]) if gplan else round_up_to_area(mothers, per_area)
		batches = [cint(x["mothers"]) for x in gplan[1:]]
		raw = mothers / max(1, gens)
		# Two ends of a range, not one date. The earliest is the old anchor --
		# the whole pool standing before the field wants anything -- and the
		# latest is generation one ramped up exactly as the season opens, with
		# the rest arriving during it. The line is walked across the range and
		# the last of the best starts wins, because every week bought early is a
		# week of mothers cut into nothing AND a week off the end of the season.
		earliest = add_days(getdate(first_stick), -7 * (c * regen + ramp_lead))
		latest = add_days(getdate(first_stick), -7 * ramp_lead)
		started, walk = best_start(
			demand, buy, c, regen, per_week, life, ramp, per_area,
			earliest=earliest, latest=latest, batches=batches)
		order_by = add_days(started, -7 * (lead + estab))
		# The line lives one motherstock life from its FIRST cut, so what it has
		# left for the season is counted from the day it starts cutting.
		cut_weeks = max(0, life - max(0, (getdate(first_stick) - started).days // 7))
		options.append({
			"cycles": c, "generations": round(gens, 2),
			"raw": int(round(raw)), "buy": buy,
			"batches": batches,
			"steps": [{"by": str(x["by"]), "mothers": cint(x["mothers"])}
			          for x in gplan],
			"weeks_to_full_pool": estab + c * regen + ramp_lead,
			"order_by": str(order_by),
			"first_cut": str(started),
			"earliest_first_cut": str(earliest),
			"latest_first_cut": str(latest),
			"idle_weeks": max(0, (getdate(first_stick) - started).days // 7),
			"weeks_met": cint(walk["weeks_met"]),
			"shortfall": cint(walk["shortfall"]),
			"cutting_weeks": cut_weeks,
			"covers_season": cint(walk["weeks_met"]) >= len(demand),
			"pool": cint(walk["peak_pool"]),
		})

	# The most multiplication that still covers every planting week. Every extra
	# one is plantlets saved, so the cheapest workable answer is the last of
	# them. Each option has now actually been walked, so this is coverage
	# measured rather than cutting weeks standing in for it.
	workable = [o for o in options if o["covers_season"]]
	suggested = workable[-1]["cycles"] if workable else max(
		options, key=lambda o: (o["weeks_met"], -o["shortfall"], -o["cycles"])
	)["cycles"]
	chosen = cint(cycles) if cycles not in (None, "") else suggested
	chosen = max(0, min(chosen, cint(max_cycles)))

	return {
		"peak_cuttings": peak,
		"peak_week": str(peak_week),
		"peak_week_label": "%s-W%02d" % peak_week.isocalendar()[:2],
		"first_sticking": str(first_stick),
		"sticking_weeks": need_weeks,
		"last_sticking": str(last_stick),
		"span_weeks": span_weeks,
		"season_cuttings": sum(demand.values()),
		"cuttings_per_mother_per_week": per_week,
		"min_planting_area": per_area,
		"establishment_weeks": estab,
		"regen_weeks": regen,
		"ramp_lead_weeks": ramp_lead,
		"life_weeks": life,
		"options": options,
		"suggested": suggested,
		"chosen": chosen,
		"pick": options[chosen],
	}

def generation_plan(demand, per_week, per_area, generations):
	"""What each generation has to be, and by when, for a given count.

	Dividing the peak evenly is wrong whenever the season does not arrive
	evenly, and seasons rarely do. Aster Pink Flash opens at 25,000 cuttings a
	week and climbs 12,000 to its peak eight weeks later, so the line it wants
	is 25,000 bought and one batch of 12,000 -- not two equal halves of 18,500,
	which is too much in January and still not enough in March.

	The pool only ever grows towards the peak, so what it must be able to cut by
	a given week is the biggest week up to and including it. The steps in that
	curve ARE the generations: each one is an increment, needed by the date the
	curve steps up.

	Returns a list, generation one first. Only generation one is bought -- the
	rest are cuttings taken off it -- so its size is the plantlet order.
	"""
	levels, carry = [], 0
	for d in sorted(demand):
		if cint(demand[d]) > carry:
			carry = cint(demand[d])
			levels.append({"by": d, "mothers": round_up_to_area(
				carry / (flt(per_week) or 1.0), per_area)})
	if not levels:
		return []
	g = max(1, cint(generations))

	if g <= len(levels):
		# Fewer generations than steps, so the earlier steps have to be bought
		# together: generation one is sized to the largest level it must cover.
		head = levels[: len(levels) - g + 1]
		tail = levels[len(levels) - g + 1:]
		out = [{"by": head[0]["by"], "mothers": head[-1]["mothers"]}]
		running = head[-1]["mothers"]
		for lv in tail:
			out.append({"by": lv["by"], "mothers": lv["mothers"] - running})
			running = lv["mothers"]
		return out

	# More generations than the season has steps, so the first level is reached
	# in instalments: buy a share of it and multiply up to it before the season
	# opens, then one generation per step after that.
	extra = g - len(levels)
	first = levels[0]["mothers"]
	share = round_up_to_area(first / float(extra + 1), per_area)
	out, running = [], 0
	for _i in range(extra + 1):
		take = min(share, first - running) if running + share > first else share
		take = max(0, take)
		out.append({"by": levels[0]["by"], "mothers": take})
		running += take
	if running < first:
		out[-1]["mothers"] += first - running
		running = first
	for lv in levels[1:]:
		out.append({"by": lv["by"], "mothers": lv["mothers"] - running})
		running = lv["mothers"]
	return [x for x in out if cint(x["mothers"]) > 0]


def _walk_line(demand, buy, c, regen, per_week, life, ramp, per_area, first_cut,
               sends_allowed=None, batches=None):
	"""One motherstock line, week by week, from a given first cut.

	The engine. Everything that reports a line goes through here, so the card,
	the document, the what-if and the order cannot walk it differently.

	The farm's rule, in its own words: even in ramp one the cut can go to
	hardening and then to the farm. So the field is served first, every week,
	from the very first cut -- a ramping pool still roots and hardens and those
	plants are plantable. Only what the field does not ask for goes back as
	mothers, and a generation leaves only when a full batch has been gathered.
	Multiplication is a thing you do a counted number of times, never more than
	MAX_MULTIPLICATIONS.

	The pool is not kept at its peak all season. A mother standing in a week that
	needs a thousand cuttings is a bed that could be growing something else, so
	mothers are cut off as soon as no week still ahead of them needs them -- the
	pool is held for the biggest week LEFT, not the biggest week of the year. It
	only ever shrinks, because a mother cut off cannot be grown back inside a
	week.

	Every generation is cleared together, one motherstock life after the FIRST
	cut, so a later one simply gets fewer weeks. They come off the same order of
	plantlets and they go at the same time.
	"""
	first_cut = getdate(first_cut)
	line_end = add_days(first_cut, 7 * cint(life))
	# Each multiplication is its own size: the step in demand it was raised for.
	# Without this every generation was a copy of the order, which is only right
	# when the season arrives in equal instalments.
	batches = [cint(b) for b in (batches or []) if cint(b) > 0]
	if not batches:
		batches = [cint(buy)] * min(cint(c), MAX_MULTIPLICATIONS)
	if sends_allowed is None:
		sends_allowed = min(len(batches), MAX_MULTIPLICATIONS)

	gens = [{"generation": 1, "mothers": buy, "live": buy, "arrivals": 1,
	         "first_cut_date": str(first_cut),
	         "cutting_weeks": cint(life),
	         "expiry_date": str(line_end), "stuck_date": None}]
	arrivals = {}          # week -> list of pending generations landing then
	batch, sends = 0, 0    # cuttings gathered so far, multiplications sent
	batch_from = None

	# The biggest week still ahead, week by week: what the pool has to be able to
	# cut from here on. Everything above it is a mother nobody will need again.
	from upande_summer_flowers.summer_flowers.lifecycle_sim import ramp_ratio

	per_area = cint(per_area) or 1
	ahead, carry = {}, 0
	for d in sorted(demand, reverse=True):
		carry = max(carry, cint(demand[d]))
		ahead[d] = carry
	keep_from = sorted(ahead)

	def keep_at(w):
		"""Mothers worth standing in week `w`, rounded up to a whole planting area."""
		nxt = next((d for d in keep_from if d >= w), None)
		if nxt is None:
			return 0
		return round_up_to_area(ahead[nxt] / per_week, per_area)

	rows, week, n, run, short = [], first_cut, 0, 0, 0
	while week < line_end:
		n += 1
		event = []
		for g in arrivals.pop(week, []):
			gens.append(g)
			event.append(_("Generation {0} starts cutting").format(g["generation"]))

		# Cut off what the rest of the season cannot use, oldest mothers first: a
		# block is thinned from the bed that has been cut longest. Only once the
		# pool is whole, so the build-up is not thinned before it is finished.
		removed = 0
		if sends >= sends_allowed and not arrivals:
			live = [g for g in gens if getdate(g["first_cut_date"]) <= week]
			standing_now = sum(cint(g["live"]) for g in live)
			spare_mothers = standing_now - keep_at(week)
			for g in sorted(live, key=lambda x: x["generation"]):
				if spare_mothers <= 0:
					break
				take = min(cint(g["live"]), spare_mothers)
				g["live"] = cint(g["live"]) - take
				spare_mothers -= take
				removed += take
			if removed:
				event.append(_("{0} mothers cut off; nothing ahead needs them")
				             .format("{:,}".format(removed)))

		standing, cut = 0, 0.0
		for g in gens:
			on = getdate(g["first_cut_date"])
			if on > week:
				continue
			standing += cint(g["live"])
			cut += cint(g["live"]) * per_week * ramp_ratio((week - on).days // 7, ramp)
		cut = int(round(cut))

		# Field first -- this is the correction. What is left over is the only
		# thing that can be multiplied, which is why a line that is already fully
		# committed to the farm cannot multiply at all and must be bought outright.
		wants = cint(demand.get(week, 0))
		to_field = min(cut, wants)
		surplus = cut - to_field
		run += to_field

		to_mult = 0
		if sends < sends_allowed and surplus > 0:
			want_batch = batches[sends]
			to_mult = min(surplus, want_batch - batch)
			if batch == 0 and to_mult:
				batch_from = week
			batch += to_mult
			if batch >= want_batch:
				sends += 1
				lands = add_days(week, 7 * regen)
				arrivals.setdefault(lands, []).append({
					"generation": len(gens) + len(
						[x for v2 in arrivals.values() for x in v2]) + 1,
					"mothers": want_batch, "live": want_batch, "arrivals": 1,
					"first_cut_date": str(lands),
					"cutting_weeks": max(0, (line_end - lands).days // 7),
					"expiry_date": str(line_end),
					"stuck_date": str(batch_from),
				})
				event.append(_("Multiplication {0} of {1} sent, {2} plants land {3}").format(
					sends, sends_allowed, "{:,}".format(want_batch), lands))
				batch, batch_from = 0, None

		if week == first_cut:
			event.insert(0, _("Generation 1 first cut"))
		rows.append({
			"week_no": n, "week_start": str(week), "event": "; ".join(event),
			"generations_live": ", ".join(
				"G%d" % g["generation"] for g in gens
				if getdate(g["first_cut_date"]) <= week),
			"mothers_standing": standing, "cuttings_cut": cut,
			"to_multiplication": to_mult,
			"to_field": to_field, "cumulative_to_field": run,
			# Cut and neither planted nor sent back. Without this column 37,000
			# was cut, 25,000 went to the field and 12,000 simply vanished off
			# the table with nothing to say where.
			"cuttings_spare": max(0, cut - to_field - to_mult),
			"mothers_removed": removed,
			"demand": wants, "shortfall": max(0, wants - to_field),
		})
		week = add_days(week, 7)
	steps, held = [], None
	for r in rows:
		if cint(r["mothers_standing"]) != held:
			held = cint(r["mothers_standing"])
			steps.append({"week_no": r["week_no"], "week_start": r["week_start"],
			              "mothers": held})

	rows.append({"week_no": n + 1, "week_start": str(line_end),
	             "event": _("Block cleared"), "generations_live": "",
	             "mothers_standing": 0, "cuttings_cut": 0,
	             "to_multiplication": 0, "to_field": 0, "cuttings_spare": 0,
	             "mothers_removed": 0,
	             "cumulative_to_field": run, "demand": 0, "shortfall": 0})
	# Over EVERY demand week, not only the ones the line is alive for. A sticking
	# week before the first cut or after the clearance has no row at all, and
	# counting the shortfall only across rows made those weeks disappear: the
	# schedule reported no shortfall while meeting 32 of 38 weeks.
	by_week = {r["week_start"]: r for r in rows}
	short = 0
	weeks_met = 0
	for d, nn in demand.items():
		got = cint((by_week.get(str(d)) or {}).get("to_field"))
		if got >= nn:
			weeks_met += 1
		else:
			short += nn - got
	by_week = {r["week_start"]: r for r in rows}
	short = 0
	weeks_met = 0
	for d, nn in demand.items():
		got = cint((by_week.get(str(d)) or {}).get("to_field"))
		if got >= nn:
			weeks_met += 1
		else:
			short += nn - got
	return {"tc": buy, "cycles": c, "generations": gens,
	        "weeks_table": rows, "to_field": run, "shortfall": short,
	        "weeks_met": weeks_met, "weeks": len(demand),
	        "sends_made": sends, "sends_allowed": sends_allowed,
	        "peak_pool": max((cint(r["mothers_standing"]) for r in rows), default=0),
	        # Thinning only. The last step takes the pool to nothing because no
	        # sticking week is left, and counting that as "cut off" made the whole
	        # pool read as thrown away.
	        "mothers_removed": sum(cint(r["mothers_removed"]) for r in rows
	                               if cint(r["mothers_standing"])),
	        "cuttings_spare": sum(cint(r["cuttings_spare"]) for r in rows),
	        # What the pool actually does over the season, which is the thing
	        # worth reading: 37,000 down to 14,000 down to 5,000, and when.
	        "pool_steps": steps,
	        "line_end_date": str(line_end),
	        "first_cut_date": str(first_cut),
	        "covers": short == 0}


def best_start(demand, buy, c, regen, per_week, life, ramp, per_area,
               earliest, latest, batches=None):
	"""The LATEST first cut that still covers as much as the earliest would.

	Starting early is not free, and the old anchor made it look free: it put the
	first cut far enough back that the WHOLE pool was standing in the first
	sticking week. At two multiplications that was twenty-five weeks of cutting
	thirteen thousand cuttings a week into nothing -- no orders, a year of
	managing mothers -- and because the block is cleared one life after its FIRST
	cut, every one of those weeks was also taken off the end of the season. The
	line died in July with sticking weeks still to come.

	It does not need the whole pool on the first day. Generation one goes to the
	farm while generation two is still rooting, which is how the farm works it,
	so the line only has to be big enough each week rather than finished before
	the season opens. Walking the candidates and keeping the last of the best
	ones buys as late as the coverage allows.
	"""
	best, week = None, getdate(earliest)
	latest = getdate(latest)
	while week <= latest:
		r = _walk_line(demand, buy, c, regen, per_week, life, ramp, per_area, week,
		               batches=batches)
		# More planting weeks met first, then fewer cuttings short. On a tie the
		# later start wins, which is the whole point: it is the same coverage for
		# less standing motherstock and more of the line left for the season.
		key = (r["weeks_met"], -r["shortfall"])
		if best is None or key >= best[0]:
			best = (key, week, r)
		week = add_days(week, 7)
	return best[1], best[2]


def peak_schedule(plan, sizing, cycles=None, tc=None, first_cut=None):
	"""The line under the peak-week sizing, started as late as it can be."""
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	demand = demand_by_sticking_week(plan)
	if not demand:
		return None
	from upande_summer_flowers.summer_flowers.lifecycle_sim import ramp_ratio  # noqa

	c = cint(sizing["chosen"] if cycles in (None, "") else cycles)
	pick = sizing["options"][min(c, len(sizing["options"]) - 1)]
	buy = cint(tc) if cint(tc) else cint(pick["buy"])
	args = (demand, buy, c, cint(sizing["regen_weeks"]),
	        flt(sizing["cuttings_per_mother_per_week"]) or 1.0,
	        cint(sizing["life_weeks"]), v.ramp_ratios() or [1.0],
	        cint(sizing["min_planting_area"]) or 1)

	batches = pick.get("batches") or None
	if first_cut:
		started = getdate(first_cut)
		out = _walk_line(*args, first_cut=started, batches=batches)
	elif buy == cint(pick["buy"]):
		started = getdate(pick["first_cut"])
		out = _walk_line(*args, first_cut=started, batches=batches)
	else:
		# An overridden order changes what each batch can be, so the steps are
		# scaled with it rather than left at the sizes the suggestion worked out.
		if batches and cint(pick["buy"]):
			f = flt(buy) / flt(pick["buy"])
			batches = [max(1, int(round(b * f))) for b in batches]
		started, out = best_start(*args,
		                          earliest=pick["earliest_first_cut"],
		                          latest=pick["latest_first_cut"],
		                          batches=batches)
	lead = cint(v.supplier_lead_weeks) + cint(sizing["establishment_weeks"])
	out["pick"] = pick
	out["order_by"] = str(add_days(started, -7 * lead))
	out["weeks_idle_before_demand"] = max(
		0, (getdate(sizing["first_sticking"]) - started).days // 7)
	return out


@frappe.whitelist()
def peak_options(production_plan, cycles=None, tc=None):
	"""What the procurement dialog draws: the peak week, and what it implies."""
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	from upande_summer_flowers.summer_flowers import sourcing

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	if not entry or not sourcing.standing_stage(v, entry):
		return {"propagates": False, "reason": decided.get("reason"),
		        "variety": plan.variety, "farm": plan.farm}
	sizing = peak_sizing(plan, cycles=cycles)
	if not sizing:
		return {"propagates": True, "solvable": False,
		        "reason": _("This plan has no new plantings, so there is no peak "
		                    "week to size an order from.")}
	sch = peak_schedule(plan, sizing, cycles=cycles, tc=tc)
	standing = sourcing.standing_pool_capacity(v, getdate(sizing["first_sticking"]))
	return {
		"propagates": True, "solvable": True, "plan": plan.name,
		"variety": plan.variety, "farm": plan.farm, "protocol": v.name,
		"sizing": sizing, "schedule": sch,
		"standing": {"plants": cint(standing)},
		"need": {"plants": sum(cint(b.plants) for b in plan.plan_blocks
		                       if cint(b.is_new_planting)),
		         "cuttings": sizing["season_cuttings"],
		         "weeks": sizing["sticking_weeks"],
		         "first_sticking": sizing["first_sticking"]},
		"assumed": _assumptions(v),
	}
