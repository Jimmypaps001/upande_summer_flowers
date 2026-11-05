# Copyright (c) 2026, James Kiruga and contributors
"""How many plantlets to buy, and how long to divert the cut before planting."""

import datetime
import math

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers import lifecycle_sim as ls
from upande_summer_flowers.summer_flowers.planning import iso_monday

# A plantlet buys one mother. The build-up is the diverted cuttings below, not a
TC_IS_ONE_MOTHER = {"multiplication_factor": 1.0, "build_up_cycles": 1}

#: The farm sends a motherstock block back to multiplication four times at the
MAX_MULTIPLICATIONS = 4


def max_multiplications():
	from upande_summer_flowers.summer_flowers.settings import max_multiplications as _m

	return _m(MAX_MULTIPLICATIONS)

MAX_TC = 2_000_000
SEARCH_CEILING_STEPS = 24


def demand_by_sticking_week(plan):
	"""{Monday of the sticking week: cuttings the propagation unit must deliver}"""
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	per_plant = flt(v.cuttings_per_plant_required) or 1.0
	out = {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not cint(b.sticking_year):
			continue
		monday = iso_monday(cint(b.sticking_year), cint(b.sticking_week))
		# The demand figure, never the trimmed one. Once an agreed order is
		wanted = cint(b.get("plants_from_demand")) or cint(b.plants)
		out[monday] = out.get(monday, 0) + int(round(wanted * per_plant))
	return dict(sorted(out.items()))


def last_useful_divert_weeks(p):
	"""Diverting after this many weeks of cutting buys mothers that never cut."""
	estab = cint(p.get("cutting_to_mother_weeks")) or cint(p["ms_establishment_weeks"])
	return max(0, cint(p["ms_life_weeks"]) - estab)


def order_date_for(p, first_sticking, divert_weeks):
	"""Back from the week the field's first cuttings must be stuck."""
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
	overrides = {first_cut_sw + i: 0 for i in range(cint(divert_weeks))}

	sim = ls.simulate(p, arriving, order, num_cycles=1, farm_overrides=overrides,
	                  demand_by_sw=demand_by_sw, horizon_weeks=last_sw + 8,
	                  # Multiplying ends when the window does. After it the cut goes
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
	"""Smallest order that covers every planting week at this diversion."""
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
			"blocked": _("Diverting {0} weeks pulls the order {0} weeks earlier, so "
			             "the block would be cleared on {1}. The season's last "
			             "planting is {2}, and the block has to outlast it. No "
			             "quantity of TC plants changes that.")
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
			"blocked": _("Even {0} TC plants do not cover every planting week at "
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
	"""The whole answer the dialog draws: one recommendation, and what it means."""
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
	usable = [o for o in options if o.get("covers")]
	placeable = [o for o in usable if not o["order_late"]]
	# Cheapest that can still be ordered. Nothing placeable means the decision is
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
		at_d = next((o for o in usable if o["divert_weeks"] == chosen_d), None)
		if not at_d:
			r = least_tc_for(plan.protocol, chosen_d, demand, first_sticking, loss)
			at_d = r if (r and r.get("covers")) else None
		chosen_tc = (at_d or pick or {}).get("tc")
	# The chosen pair comes back with its whole schedule, because the week list is
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
		# The cost case, said plainly: multiplying our own mothers is what makes
		# a season cheap, and the comparison is what proves it. A season whose
		# order weeks have all gone says so here rather than in a date nobody
		# reads.
		"buying": _buying(options, usable, placeable, pick),
		"recommended": pick,
		"chosen": detail,
		"options": options,
		"limits": {
			# What the protocol allows, and what THIS season allows. The second is
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
		"standing": {
			"plants": cint(standing),
			"as_at": str(first_sticking),
		},
		"assumed": _assumptions(v),
	}


def _buying(options, usable, placeable, pick):
	"""How much of the season can still be bought, and what multiplying saves."""
	flat = next((o for o in usable if not cint(o.get("divert_weeks"))), None)
	cheapest_open = (min(placeable, key=lambda o: (o["tc"], -o["divert_weeks"]))
	                 if placeable else None)
	saved = None
	if flat and pick and flat.get("tc") and pick.get("tc"):
		saved = {"tc_without_multiplying": cint(flat["tc"]),
		         "tc_with_multiplying": cint(pick["tc"]),
		         "tc_saved": max(0, cint(flat["tc"]) - cint(pick["tc"])),
		         "times_fewer": (round(flt(flat["tc"]) / flt(pick["tc"]), 1)
		                         if flt(pick["tc"]) else None),
		         "cost_without": flat.get("cost"), "cost_with": pick.get("cost")}
	return {
		"options": len(options),
		"covering": len(usable),
		"still_open": len(placeable),
		"all_order_weeks_passed": bool(usable and not placeable),
		"cheapest_still_open": cheapest_open,
		"saving": saved,
	}


def _assumptions(v):
	"""Protocol figures that are defaults rather than measurements."""
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
		out.append(_("Supplier lead is not set, so the TC plants are treated as "
		             "arriving the week they are ordered."))
	if not flt(v.tc_order_loss_pct):
		out.append(_("No TC order loss allowance, so every TC plant ordered is "
		             "assumed to survive the transfer."))
	return out


@frappe.whitelist()
def options(production_plan, tc=None, divert_weeks=None):
	"""Everything the propagation dialog draws, for one production plan."""
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	return recommend(plan, tc=tc, divert_weeks=divert_weeks)


def _generation_of(start_sw, first_cut_sw, estab):
	"""Which generation a pool coming online in `start_sw` belongs to."""
	if not estab or start_sw <= first_cut_sw:
		return 1
	return 1 + max(1, int(round((start_sw - first_cut_sw) / float(estab))))


def schedule_for(version, tc, divert_weeks, demand, first_sticking, tc_loss_pct=0.0):
	"""The line week by week, and the generations that make it up."""
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

def round_up_to_area(plants, per_area):
	"""Up to a whole minimum planting area. Nobody buys a third of a bed."""
	if not per_area or per_area <= 0:
		return int(math.ceil(plants))
	return int(math.ceil(plants / float(per_area)) * per_area)


def peak_sizing(plan, cycles=None, max_cycles=None):
	"""What to buy, sized by the busiest week, and how often to multiply."""
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	if max_cycles in (None, ""):
		# The protocol gets to say so when it has said so. Most versions carry a
		max_cycles = cint(v.max_multiplication_cycles) or max_multiplications()
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
	# What a propagation order is rounded to, which is NOT the planting area.
	order_qty = cint(v.get("min_order_qty")) or 1
	life = cint(v.motherstock_life_weeks)
	need_weeks = len(demand)
	# The span the line has to stay alive across, not how many weeks are in it.
	last_stick = max(demand)
	span_weeks = (getdate(last_stick) - getdate(first_stick)).days // 7
	# A generation ramps before it cuts at full rate, so the pool has to START
	ramp_lead = max(0, len(v.ramp_ratios() or [1.0]) - 1)

	ramp = v.ramp_ratios() or [1.0]
	options = []
	for c in range(0, cint(max_cycles) + 1):
		# The pool divided by the generations standing. That is all it is, and
		mothers = peak / per_week
		split = even_generations(mothers, c + 1, order_qty) or [
			round_up_to_area(mothers, order_qty)]
		buy, batches = cint(split[0]), [cint(x) for x in split[1:]]
		gens = len(split)
		raw = mothers / max(1, gens)
		gplan = [{"by": first_stick, "mothers": buy}] + [
			{"by": first_stick, "mothers": b} for b in batches]

		# Two ends of a range, not one date. The earliest is the old anchor --
		gather = max(1, int(math.ceil(1.0 / (flt(per_week) or 1.0))))
		earliest = add_days(getdate(first_stick),
		                    -7 * (c * (regen + gather + ramp_lead) + ramp_lead))
		latest = add_days(getdate(first_stick), -7 * ramp_lead)
		started, walk = best_start(
			demand, buy, c, regen, per_week, life, ramp, per_area,
			earliest=earliest, latest=latest, batches=batches or None)

		order_by = add_days(started, -7 * (lead + estab))
		# The line lives one motherstock life from its FIRST cut, so what it has
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

	# What the protocol says, and nothing cleverer. "Max times re-rooted" is
	suggested = max(0, min(cint(max_cycles), len(options) - 1))
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
		"min_order_qty": order_qty,
		"establishment_weeks": estab,
		"regen_weeks": regen,
		"ramp_lead_weeks": ramp_lead,
		"life_weeks": life,
		"options": options,
		# The cap that actually applied, which is the protocol's where it states
		"max_cycles": cint(max_cycles),
		"suggested": suggested,
		"chosen": chosen,
		"pick": options[chosen],
	}

def even_generations(mothers, generations, per_area):
	"""The peak in equal instalments, which is how the farm says it."""
	g = max(1, cint(generations))
	need = int(math.ceil(flt(mothers)))
	if need <= 0:
		return []
	each = round_up_to_area(need / float(g), per_area)
	out, running = [], 0
	for _ in range(g):
		if running >= need:
			break
		take = round_up_to_area(min(each, need - running), per_area)
		out.append(take)
		running += take
	return out

def _walk_line(demand, buy, c, regen, per_week, life, ramp, per_area, first_cut,
               sends_allowed=None, batches=None):
	"""One motherstock line, week by week, from a given first cut."""
	first_cut = getdate(first_cut)
	line_end = add_days(first_cut, 7 * cint(life))
	# Each multiplication is its own size: the step in demand it was raised for.
	given = [cint(b) for b in (batches or []) if cint(b) > 0]
	batches = given or [cint(buy)] * min(cint(c), max_multiplications())
	if sends_allowed is None:
		# The farm's four is the DEFAULT, not a law. A caller that hands over an
		sends_allowed = len(batches) if given else min(len(batches),
		                                              max_multiplications())

	gens = [{"generation": 1, "mothers": buy, "live": buy, "arrivals": 1,
	         "first_cut_date": str(first_cut),
	         "cutting_weeks": cint(life),
	         "expiry_date": str(line_end), "stuck_date": None}]
	arrivals = {}          # week -> list of pending generations landing then
	batch, sends = 0, 0    # cuttings gathered so far, multiplications sent
	batch_from = None

	# The biggest week still ahead, week by week: what the pool has to be able to
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

	# Taken before the clearance row is added. That row is a note, not a week of
	walked = {r["week_start"] for r in rows}

	rows.append({"week_no": n + 1, "week_start": str(line_end),
	             "event": _("Block cleared"), "generations_live": "",
	             "mothers_standing": 0, "cuttings_cut": 0,
	             "to_multiplication": 0, "to_field": 0, "cuttings_spare": 0,
	             "mothers_removed": 0,
	             "cumulative_to_field": run, "demand": 0, "shortfall": 0})
	# A sticking week the line never reaches is still a week the field wants
	for d, nn in sorted(demand.items()):
		if str(d) in walked:
			continue
		before = getdate(d) < first_cut
		rows.append({
			"week_no": 0, "week_start": str(d),
			"event": (_("Before the first cut: no mother is standing yet")
			          if before else
			          _("After the block is cleared: this line cannot reach it")),
			"generations_live": "", "mothers_standing": 0, "cuttings_cut": 0,
			"to_multiplication": 0, "to_field": 0, "cuttings_spare": 0,
			"mothers_removed": 0, "cumulative_to_field": 0 if before else run,
			"demand": cint(nn), "shortfall": cint(nn),
		})
	rows.sort(key=lambda r: r["week_start"])
	for i, r in enumerate(rows, 1):
		r["week_no"] = i

	# Over EVERY demand week, not only the ones the line is alive for -- and
	by_week = {}
	for r in rows:
		by_week[r["week_start"]] = by_week.get(r["week_start"], 0) + cint(r["to_field"])
	short = 0
	weeks_met = 0
	for d, nn in demand.items():
		got = cint(by_week.get(str(d), 0))
		if got >= nn:
			weeks_met += 1
		else:
			short += nn - got
	# How long each generation is actually CUT, which is not how long it could
	last_cut = None
	for r in rows:
		if flt(r.get("cuttings_cut")):
			last_cut = getdate(r["week_start"])
	for g in gens:
		life_weeks = cint(g.get("cutting_weeks"))
		g["cutting_weeks_possible"] = life_weeks
		on = getdate(g.get("first_cut_date")) if g.get("first_cut_date") else None
		g["cutting_weeks"] = (max(0, ((last_cut - on).days // 7) + 1)
		                      if (last_cut and on and last_cut >= on) else 0)

	return {"tc": buy, "cycles": c, "generations": gens,
	        "weeks_table": rows, "to_field": run, "shortfall": short,
	        "weeks_met": weeks_met, "weeks": len(demand),
	        "sends_made": sends, "sends_allowed": sends_allowed,
	        "peak_pool": max((cint(r["mothers_standing"]) for r in rows), default=0),
	        # Thinning only. The last step takes the pool to nothing because no
	        "mothers_removed": sum(cint(r["mothers_removed"]) for r in rows
	                               if cint(r["mothers_standing"])),
	        "cuttings_spare": sum(cint(r["cuttings_spare"]) for r in rows),
	        # What the pool actually does over the season, which is the thing
	        "pool_steps": steps,
	        "line_end_date": str(line_end),
	        "first_cut_date": str(first_cut),
	        "covers": short == 0}


def best_start(demand, buy, c, regen, per_week, life, ramp, per_area,
               earliest, latest, batches=None):
	"""The LATEST first cut that still covers as much as the earliest would."""
	best, week = None, getdate(earliest)
	latest = getdate(latest)
	while week <= latest:
		r = _walk_line(demand, buy, c, regen, per_week, life, ramp, per_area, week,
		               batches=batches)
		# More planting weeks met first, then fewer cuttings short. On a tie the
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
	# The options stop at the protocol's cap, so a bigger ask was already being
	c = max(0, min(c, len(sizing["options"]) - 1))
	pick = sizing["options"][c]
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


def multiplications_for_tc(plan, tc, max_search=24):
	"""How many times a given order has to be multiplied to still cover the season."""
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	demand = demand_by_sticking_week(plan)
	if not demand or cint(tc) <= 0:
		return None
	tc = cint(tc)
	per_week = flt(v.cuttings_per_plant_per_week) or 1.0
	per_area = cint(v.plants_per_bed) or cint(v.min_planting_area_sqm) or 1
	# What a propagation order is rounded to, which is NOT the planting area.
	order_qty = cint(v.get("min_order_qty")) or 1
	regen = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)
	life = cint(v.motherstock_life_weeks)
	ramp = v.ramp_ratios() or [1.0]
	ramp_lead = max(0, len(ramp) - 1)
	first_stick = min(demand)
	peak_mothers = max(demand.values()) / per_week

	# The arithmetic floor: generations of the order's own size, enough of them
	floor = max(0, int(math.ceil(peak_mothers / float(tc))) - 1)

	def batches_for(c):
		"""Rounds of at most one order each, the last trimmed to what is left."""
		out, running = [], float(tc)
		for _i in range(c):
			if running >= peak_mothers:
				break
			take = min(float(tc), peak_mothers - running)
			out.append(max(1, round_up_to_area(take, order_qty)))
			running += out[-1]
		return out

	tried, best = [], None
	for c in range(floor, min(floor + cint(max_search), 60) + 1):
		# A generation needs time to be GATHERED as well as grown. The window
		gather = max(1, int(math.ceil(1.0 / (flt(per_week) or 1.0))))
		# Every generation climbs its own build-up, not just the last one. The
		earliest = add_days(getdate(first_stick),
		                    -7 * (c * (regen + gather + ramp_lead) + ramp_lead))
		latest = add_days(getdate(first_stick), -7 * ramp_lead)
		started, walk = best_start(
			demand, tc, c, regen, per_week, life, ramp, per_area,
			earliest=earliest, latest=latest, batches=batches_for(c))
		row = {
			"cycles": c,
			"weeks_met": cint(walk["weeks_met"]),
			"weeks": cint(walk["weeks"]),
			"shortfall": cint(walk["shortfall"]),
			"covers": bool(walk["covers"]),
			"first_cut": str(started),
			"order_by": str(add_days(started, -7 * (
				cint(v.supplier_lead_weeks) + cint(v.weeks_tc_to_first_cut())))),
			"peak_pool": cint(walk["peak_pool"]),
			"sends_made": cint(walk["sends_made"]),
		}
		tried.append(row)
		if best is None or (row["weeks_met"], -row["shortfall"]) > (
				best[0]["weeks_met"], -best[0]["shortfall"]):
			best = (row, walk, started)
		if row["covers"]:
			best = (row, walk, started)
			break

	if not best:
		return None
	row, walk, started = best
	walk["pick"] = {"cycles": row["cycles"], "buy": tc,
	                "cutting_weeks": max(0, life - max(
		                0, (getdate(first_stick) - started).days // 7))}
	walk["order_by"] = row["order_by"]
	walk["weeks_idle_before_demand"] = max(
		0, (getdate(first_stick) - started).days // 7)
	return {
		"tc": tc,
		"needed": row["cycles"],
		"beyond_protocol": row["cycles"] > max_multiplications(),
		"max_usual": max_multiplications(),
		"covers": row["covers"],
		"tried": tried,
		"schedule": walk,
		# Which weeks the cuttings go back rather than to the field, and how
		"to_propagation": [
			{"week_no": r["week_no"], "week_start": r["week_start"],
			 "cuttings": cint(r["to_multiplication"]), "event": r["event"]}
			for r in walk["weeks_table"] if cint(r["to_multiplication"])
		],
	}


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
