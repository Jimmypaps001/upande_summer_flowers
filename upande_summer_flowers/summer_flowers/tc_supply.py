# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Tissue culture arrives in batches, from more than one supplier.

A lab does not send a year's order in one crate. It sends on its own rhythm --
the farm's own records show Stokman every four weeks, thirteen times a year, and
Vitroflora every four or five, while Iribov sends twenty-five times at one to
three week gaps -- and the farm buys from several at once, splitting a variety's
requirement between them.

So an order is not a number. It is a supplier, a cycle, and a run of batches,
and the question the planner has to answer is how much goes in each batch. Equal
splits are the habit and they are wrong wherever the season is not flat: a batch
landing in a week that wants 25,000 plants and a batch landing in a week that
wants 1,000 should not be the same size.

This sizes each batch by the demand it actually serves -- the planting weeks
from when it lands until the next batch lands -- and then scales the run to
whatever the supplier was allocated, so the figures still add up to what was
agreed with them.

Shape of an allocation:

	{"supplier": "Stokman", "tc": 286000, "cycle_weeks": 4,
	 "first_week": "2027-02-08"}
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

from upande_summer_flowers.summer_flowers.root_line import _monday, iso


def batch_plan(plan, allocations, lead_weeks=None, to_ground_weeks=None,
               tc_target=None):
	"""Every supplier's batches, sized by the demand each one serves.

	`tc_target` is what the labs are between them meant to send. It is not the
	plant count: roots cover part of the plan, so measuring a tissue culture
	allocation against plants wanted reports a shortfall that is simply the
	roots doing their job.
	"""
	if isinstance(allocations, str):
		allocations = frappe.parse_json(allocations or "[]")
	allocations = [a for a in (allocations or []) if cint(a.get("tc"))]
	if not allocations:
		return None

	need = _plants_by_week(plan)
	if not need:
		return None
	lead, ground = _timing(plan, lead_weeks, to_ground_weeks)
	land_lag = lead + ground

	out, grand = [], 0
	for a in allocations:
		run = _one_supplier(a, need, land_lag, lead=lead)
		run["_cycle"] = max(1, cint(a.get("cycle_weeks")) or 4)
		run["_waves"] = handover_waves("Tissue Culture", a.get("supplier"), ground)
		run["lifts_per_consignment"] = len(run["_waves"])
		grand += run["tc_total"]
		out.append(run)

	# What the propagation unit can take is a harder constraint than anything a
	# lab can promise. Lead times were respected and capacity was not, so four
	# deliveries of a season put half a million plants into one week against a
	# unit whose largest intake ever recorded is 185,000.
	keep = survival()
	limits = capacity_limits()
	# Material occupies the unit until the LAST lift comes off it. A unit that
	# states six weeks of hardening is stating when the first lift is ready, not
	# when the bench is clear, so the longer of the two is what the space is
	# actually committed for.
	limits["hold_weeks"] = max(
		[cint(limits.get("hold_weeks")), ground]
		+ [w for run in out for w, _s in run["_waves"]])
	fitted = _fit_intake(out, limits["weekly_intake"], lead)
	for run in out:
		_renumber(run)
		for b in run["batches"]:
			b["lifts"] = _lift_batch(b, run["_waves"], keep)
	handovers = _cover(out, need)
	grand = sum(run["tc_total"] for run in out)
	capacity = capacity_view(out, limits, lead)
	for k in ("units", "from", "intake_is_observed", "silent", "unstated_hold"):
		capacity[k] = limits.get(k)
	capacity["reshaped"] = fitted

	wanted = sum(need.values())
	target = cint(tc_target) if cint(tc_target) else wanted
	for run in out:
		run.pop("_waves", None)
		run.pop("_cycle", None)
	return {
		"capacity": capacity,
		"survival_pct": round(keep * 100, 2),
		"plants_from_allocation": sum(cint(w["plants"]) for w in handovers),
		# Every lift of every consignment on one timeline: what the propagation
		# unit actually hands the farm, and when.
		"handovers": handovers,
		"variety": plan.variety, "farm": plan.farm,
		"lead_weeks": lead, "to_ground_weeks": ground,
		"plants_needed": wanted,
		"tc_target": target,
		"measured_against": "the TC order" if cint(tc_target) else "plants wanted",
		"tc_allocated": grand,
		"short_by": max(0, target - grand),
		"over_by": max(0, grand - target),
		"suppliers": out,
		# Every batch from every supplier on one timeline, which is the thing a
		# buyer reads: what is due to whom in which week.
		"calendar": sorted(
			[dict(supplier=s["supplier"], **b) for s in out for b in s["batches"]],
			key=lambda b: (b["week"], b["supplier"])),
	}


def _one_supplier(alloc, need, land_lag, lead=None):
	"""One supplier's run of batches, weighted by what each one feeds."""
	tc = cint(alloc.get("tc"))
	cycle = max(1, cint(alloc.get("cycle_weeks")) or 4)
	last_land = max(need)
	# Default the first send to a lead-time BEFORE the first planting, not to
	# the planting itself. Starting on the planting week means every batch lands
	# after the week it was meant to feed, and the run reads as though most
	# batches serve nothing.
	first = _monday(alloc.get("first_week")
	                or add_days(min(need), -7 * land_lag))

	# Send weeks: start at the first, step by the cycle, and stop once a batch
	# would land after the last planting week it could possibly serve.
	weeks, w = [], first
	while add_days(w, 7 * land_lag) <= last_land:
		weeks.append(w)
		w = add_days(w, 7 * cycle)
	if not weeks:
		weeks = [first]

	# How many consignments the lab is to send, if the planner has said. The
	# cycle says how OFTEN it can send; it does not say how many times, and a
	# four-week cycle across a season is eleven sends, not four. Somebody who
	# wants four deliveries has to be able to ask for four deliveries.
	# They are spread across the run rather than taken from the front, so the
	# last one still reaches the last planting week.
	cap = cint(alloc.get("batches"))
	if cap and cap < len(weeks):
		step = (len(weeks) - 1) / float(cap - 1) if cap > 1 else 0
		weeks = [weeks[int(round(i * step))] for i in range(cap)]

	# What each batch feeds: the planting weeks from its landing until the next
	# batch lands. A batch is for the weeks only it can reach.
	spans, served = [], []
	for i, sw in enumerate(weeks):
		start = add_days(sw, 7 * land_lag)
		end = (add_days(weeks[i + 1], 7 * land_lag) if i + 1 < len(weeks)
		       else add_days(last_land, 7))
		plants = sum(q for d, q in need.items() if start <= d < end)
		spans.append((start, end))
		served.append(plants)

	total_served = sum(served) or 1
	batches, running = [], 0
	for i, sw in enumerate(weeks):
		# The last batch takes the remainder, so the run adds to the allocation
		# exactly rather than to the nearest rounding error.
		qty = (tc - running if i == len(weeks) - 1
		       else int(round(tc * served[i] / float(total_served))))
		qty = max(0, qty)
		running += qty
		# Two different weeks, which were one: the material ARRIVES at the
		# propagation unit a lead time after it is sent, and becomes plants a
		# hardening time after that. Capacity is about the first; the planting
		# week is about the second. Reporting only the second hid every question
		# about whether the unit could take what was coming.
		arrives = add_days(sw, 7 * cint(lead if lead is not None else 0))
		batches.append({
			"batch": i + 1, "week": str(sw), "week_iso": iso(sw),
			"arrives": str(arrives), "arrives_iso": iso(arrives),
			"tc": qty,
			"lands": str(spans[i][0]), "lands_iso": iso(spans[i][0]),
			"serves_to": str(add_days(spans[i][1], -7)),
			"plants_served": cint(served[i]),
		})
	# A batch that feeds nothing is not a batch. It happens when a supplier's
	# cycle is finer than the season's gaps, and listing it as an order of zero
	# invites somebody to send one.
	batches = [b for b in batches if b["tc"]]
	for i, b in enumerate(batches, start=1):
		b["batch"] = i
	return {
		"supplier": alloc.get("supplier"),
		"cycle_weeks": cycle,
		"batches_asked": cap or None,
		"batch_count": len(batches),
		"tc_total": sum(b["tc"] for b in batches),
		"tc_allocated": tc,
		"even_split": int(round(tc / float(len(batches)))) if batches else 0,
		"batches": batches,
	}


def _plants_by_week(plan):
	"""Plants wanted, by the Monday of the week they go in the ground."""
	out = {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		d = _monday(b.planting_date)
		out[d] = out.get(d, 0) + cint(b.plants)
	return out


def _timing(plan, lead_weeks, to_ground_weeks):
	"""How long from ordering to a plantable plant, off the protocol's TC chain."""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	if lead_weeks not in (None, "") and to_ground_weeks not in (None, ""):
		return cint(lead_weeks), cint(to_ground_weeks)
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	rows = cp.route_rows(v) or []
	lead, ground, seen = 0, 0, False
	for r in rows:
		st = r.get("stage") or ""
		if st == "TC" and not seen:
			seen = True
			lead = cint(r.get("lead_weeks"))
			continue
		if not seen:
			continue
		if st == "Plants":
			break
		ground += cint(r.get("weeks"))
	return (cint(lead_weeks) if lead_weeks not in (None, "") else lead,
	        cint(to_ground_weeks) if to_ground_weeks not in (None, "") else ground)


@frappe.whitelist()
def tc_suppliers(search=None, all=0):
	"""The labs this site can actually order from.

	A free-text supplier field turns a typo into a supplier nobody can raise an
	order against, so the card offers what exists.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	f = {}
	if search:
		f["name"] = ["like", "%%%s%%" % search]
	# Suppliers awaiting approval are offered too, and named as pending. This
	# site auto-disables a supplier until its workflow reaches Approved, so
	# filtering them out meant somebody could create a lab and then not find it,
	# with nothing on screen saying why. Hiding it does not stop the order; it
	# stops the explanation.
	rows = frappe.get_all(
		"Supplier", filters=f,
		fields=["name", "disabled", "workflow_state"],
		order_by="disabled asc, name asc",
		limit=0 if cint(all) else 40)
	return {
		"names": [r.name for r in rows],
		"pending": [r.name for r in rows if cint(r.disabled)],
	}


@frappe.whitelist()
def batches_for(production_plan, allocations=None, lead_weeks=None,
                to_ground_weeks=None, tc_target=None):
	"""What the dashboard draws for a multi-supplier tissue culture order."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	return batch_plan(plan, allocations, lead_weeks=lead_weeks,
	                  to_ground_weeks=to_ground_weeks, tc_target=tc_target)


# ---------------------------------------------------------------------------
# What the propagation unit can actually take
# ---------------------------------------------------------------------------

def propagation_units(form="Tissue Culture"):
	"""The warehouses plants are raised in, with what each one can take.

	A propagation unit holds stock, so it is a warehouse, and its capacity
	belongs on it rather than in one global figure: Kudenga holds 756,000 and
	takes both forms, Bondet holds 192,500 and has only ever taken roots, and
	Plantech is an outside propagator that has never stated a figure at all.

	Every farm's material goes through more than one of them and the split is by
	form, so there is no unit to pick for a plan. They are a pool, and a delivery
	programme is measured against the pool.
	"""
	takes = ("custom_sf_takes_roots" if form == "Roots" else "custom_sf_takes_tc")
	rows = frappe.get_all(
		"Warehouse",
		filters={"custom_sf_is_propagation_unit": 1, "disabled": 0, takes: 1},
		fields=["name", "custom_sf_capacity_plants", "custom_sf_tc_capacity_plants",
		        "custom_sf_roots_capacity_plants", "custom_sf_weekly_intake_plants",
		        "custom_sf_observed_peak_intake", "custom_sf_hold_weeks_tc",
		        "custom_sf_hold_weeks_roots"])
	out = []
	for r in rows:
		per_form = cint(r.custom_sf_roots_capacity_plants if form == "Roots"
		                else r.custom_sf_tc_capacity_plants)
		# A stated limit on a week's intake, or what the unit has actually taken
		# in its heaviest week. The second is evidence rather than a promise, so
		# which one is being used is reported and never quietly mixed in.
		out.append({
			"warehouse": r.name,
			"holds": per_form or cint(r.custom_sf_capacity_plants),
			"holds_is_per_form": bool(per_form),
			"weekly_intake": cint(r.custom_sf_weekly_intake_plants),
			"observed_peak": cint(r.custom_sf_observed_peak_intake),
			"hold_weeks": cint(r.custom_sf_hold_weeks_roots if form == "Roots"
			                   else r.custom_sf_hold_weeks_tc),
		})
	return out


def capacity_limits(form="Tissue Culture"):
	"""The pool's ceilings. Zero means nobody has said, and nothing is applied.

	Read from the propagation warehouses where there are any, and from the
	settings where there are none -- the settings figures were the first way of
	saying this and a site that has not drawn its units yet still has them.
	"""
	units = propagation_units(form)
	stated = [u for u in units if u["weekly_intake"]]
	observed = [u for u in units if not u["weekly_intake"] and u["observed_peak"]]
	if units:
		return {
			"hold_plants": sum(u["holds"] for u in units),
			"weekly_intake": (sum(u["weekly_intake"] for u in stated)
			                  + sum(u["observed_peak"] for u in observed)),
			"hold_weeks": max([u["hold_weeks"] for u in units] + [0]),
			"units": units,
			"from": "warehouses",
			"intake_is_observed": bool(observed and not stated),
			"silent": [u["warehouse"] for u in units
			           if not u["weekly_intake"] and not u["observed_peak"]],
			"unstated_hold": [u["warehouse"] for u in units if not u["holds"]],
		}
	return {
		"hold_plants": cint(frappe.db.get_single_value(
			"Summer Flower Settings", "propagation_capacity_plants")),
		"weekly_intake": cint(frappe.db.get_single_value(
			"Summer Flower Settings", "max_weekly_intake_plants")),
		"hold_weeks": cint(frappe.db.get_single_value(
			"Summer Flower Settings", "propagation_hold_weeks")),
		"units": [], "from": "settings", "intake_is_observed": False,
		"silent": [], "unstated_hold": [],
	}


def _intake(suppliers):
	"""Everything arriving at the unit in a week, whoever sent it."""
	by = {}
	for s in suppliers:
		for b in s["batches"]:
			by[b["arrives"]] = by.get(b["arrives"], 0) + cint(b["tc"])
	return by


def _fit_intake(suppliers, cap, lead):
	"""Nothing arrives in a week the unit cannot set down.

	What will not fit moves EARLIER, never later. A batch exists to be ready for
	a planting week, so a batch that slips arrives after the week it was for;
	one that comes early only costs holding time. It goes back onto the
	supplier's own cycle, which is the only week that supplier can send in.

	Returns what had to be moved, because a plan quietly reshaped is a plan
	nobody checked.
	"""
	report = {"moved": 0, "unplaceable": 0, "weeks": 0}
	if not cap:
		return report
	for _ in range(500):
		intake = _intake(suppliers)
		over = [w for w in sorted(intake) if intake[w] > cap]
		if not over:
			break
		# Latest first: moving a late week's excess back opens nothing behind
		# it, but moving an early one can overflow the week it lands on, and
		# that week is then still ahead of the loop.
		week = over[-1]
		excess = intake[week] - cap
		report["weeks"] += 1
		for s in sorted(suppliers,
		                key=lambda s: -sum(b["tc"] for b in s["batches"]
		                                   if b["arrives"] == week)):
			if excess <= 0:
				break
			excess -= _push_back(s, suppliers, week, excess, cap, lead)
		if excess > 0:
			report["unplaceable"] += excess
			break
		report["moved"] += intake[week] - cap
	return report


def _push_back(run, suppliers, week, excess, cap, lead):
	"""Move up to `excess` out of this supplier's batch in `week`, earlier."""
	here = [b for b in run["batches"] if b["arrives"] == week]
	if not here:
		return 0
	cycle = cint(run.get("_cycle")) or 4
	shifted = 0
	for b in here:
		if excess - shifted <= 0:
			break
		take = min(b["tc"], excess - shifted)
		# Earlier slots on this supplier's own cycle, nearest first. A slot the
		# supplier already uses is preferred; a new one is minted behind the
		# first only when the used ones are full.
		slot = add_days(getdate(b["arrives"]), -7 * cycle)
		placed = 0
		for _ in range(60):
			if placed >= take:
				break
			room = cap - _intake(suppliers).get(str(slot), 0)
			if room > 0:
				put = min(room, take - placed)
				_add_to(run, slot, put, cycle, lead)
				placed += put
			slot = add_days(slot, -7 * cycle)
		b["tc"] -= placed
		shifted += placed
	run["batches"] = [b for b in run["batches"] if b["tc"] > 0]
	_renumber(run)
	return shifted


def _add_to(run, arrives, qty, cycle, lead):
	"""Put `qty` into this supplier's batch arriving that week, or open one."""
	key = str(arrives)
	for b in run["batches"]:
		if b["arrives"] == key:
			b["tc"] += qty
			return
	send = add_days(getdate(arrives), -7 * cint(lead or 0))
	run["batches"].append({
		"batch": 0, "week": str(send), "week_iso": iso(send),
		"arrives": key, "arrives_iso": iso(arrives),
		"tc": qty,
		"lands": None, "lands_iso": None,
		"serves_to": None, "plants_served": 0,
		"added_for_capacity": 1,
	})


def _cover(out, need):
	"""How far into the season each lift takes us, counted cumulatively.

	A batch used to be credited with the planting weeks between its own landing
	and the next batch's. That reads well while every batch sits beside the
	weeks it was sized for, and it stops being true the moment capacity moves
	one earlier -- a consignment brought forward still buys the weeks it was
	always for. It also could not survive a consignment arriving as two lifts
	weeks apart.

	So supply and demand are both run as totals, across every supplier and every
	lift in the order the plants actually appear. A lift covers the planting
	weeks its arrival takes the running supply past, which is the question a
	buyer is asking: how far am I covered once this one is off.
	"""
	lifts = []
	for run in out:
		for b in run["batches"]:
			for w in b.get("lifts") or []:
				lifts.append((w["ready"], run["supplier"], b, w))
	lifts.sort(key=lambda x: (x[0], str(x[1] or "")))

	wanted = sorted(need.items())
	supply = seen = 0
	idx = 0
	timeline = []
	for ready, supplier, b, w in lifts:
		supply += cint(w["plants"])
		covered = seen
		while idx < len(wanted) and covered + wanted[idx][1] <= supply:
			covered += wanted[idx][1]
			idx += 1
		w["plants_served"] = cint(covered - seen)
		w["serves_to"] = str(wanted[idx - 1][0]) if idx else None
		seen = covered
		timeline.append({
			"supplier": supplier, "batch": b["batch"], "lift": w["lift"],
			"week": w["ready"], "week_iso": w["ready_iso"],
			"plants": w["plants"], "share_pct": w["share_pct"],
			"weeks_after_arrival": w["weeks_after_arrival"],
			"plants_served": w["plants_served"], "serves_to": w["serves_to"],
		})

	# Roll the lifts back up so a batch row still says what it bought.
	for run in out:
		for b in run["batches"]:
			ls = b.get("lifts") or []
			b["plants_served"] = sum(cint(w.get("plants_served")) for w in ls)
			b["serves_to"] = next((w["serves_to"] for w in reversed(ls)
			                       if w.get("serves_to")), None)
			b["lands"] = ls[0]["ready"] if ls else b.get("lands")
			b["lands_iso"] = ls[0]["ready_iso"] if ls else b.get("lands_iso")
			b["last_lift"] = ls[-1]["ready"] if ls else None
			b["last_lift_iso"] = ls[-1]["ready_iso"] if ls else None
	return timeline


def _renumber(run):
	run["batches"].sort(key=lambda b: b["week"])
	for i, b in enumerate(run["batches"], start=1):
		b["batch"] = i
	run["batch_count"] = len(run["batches"])
	run["tc_total"] = sum(b["tc"] for b in run["batches"])


def capacity_view(suppliers, limits, lead):
	"""Week by week: what comes in, what is being held, and what will not fit."""
	intake = _intake(suppliers)
	hold = cint(limits.get("hold_weeks"))
	rows, running = [], []
	weeks = sorted(intake)
	if not weeks:
		return {"weeks": [], "peak_intake": 0, "peak_held": 0, "breaches": 0}
	first, last = getdate(weeks[0]), getdate(weeks[-1])
	span = last
	if hold:
		span = add_days(last, 7 * hold)
	d = first
	peak_in = peak_held = 0
	breaches = 0
	while d <= span:
		got = intake.get(str(d), 0)
		running.append((d, got))
		held = sum(q for wk, q in running
		           if not hold or (d - wk).days < 7 * hold)
		over_in = bool(limits["weekly_intake"] and got > limits["weekly_intake"])
		over_hold = bool(limits["hold_plants"] and held > limits["hold_plants"])
		if over_in or over_hold:
			breaches += 1
		peak_in = max(peak_in, got)
		peak_held = max(peak_held, held)
		if got or over_hold:
			rows.append({
				"week": str(d), "week_iso": iso(d), "intake": got, "held": held,
				"over_intake": 1 if over_in else 0,
				"over_hold": 1 if over_hold else 0,
			})
		d = add_days(d, 7)
	return {
		"weeks": rows, "peak_intake": peak_in, "peak_held": peak_held,
		"breaches": breaches,
		"intake_cap": limits["weekly_intake"], "hold_cap": limits["hold_plants"],
		"hold_weeks": hold,
		"intake_headroom": (limits["weekly_intake"] - peak_in
		                    if limits["weekly_intake"] else None),
		"hold_headroom": (limits["hold_plants"] - peak_held
		                  if limits["hold_plants"] else None),
	}


# ---------------------------------------------------------------------------
# A consignment does not become plants all at once
# ---------------------------------------------------------------------------

def handover_waves(form="Tissue Culture", supplier=None, default_weeks=0):
	"""[(weeks after it arrives, share of the consignment), ...].

	Grade 1 and Grade 2 in the planning workbook are not qualities. They are the
	first batch of plants off a consignment and the second: Iribov hands over
	87.7% of a shipment seven weeks after it arrives and the rest two weeks
	later, roots 71.5% at seventeen weeks and 28.5% at twenty. The planner used
	to treat a consignment as becoming plants in one week, which is why a
	delivery that fed a fortnight of planting looked like it fed one.

	A supplier's own rules win. Where it has none the blank-supplier rules apply,
	and where there are none of those at all the old behaviour stands: everything
	in one lift, at the protocol's weeks to ground.
	"""
	rows = frappe.get_all(
		"Summer Flower Handover Wave",
		filters={"parent": "Summer Flower Settings", "form": form},
		fields=["source", "lift_no", "weeks_after_arrival", "share_pct"],
		order_by="lift_no asc")
	mine = [r for r in rows if supplier and r.source == supplier]
	if not mine:
		mine = [r for r in rows if not r.source]
	if not mine:
		return [(cint(default_weeks), 1.0)]
	total = sum(flt(r.share_pct) for r in mine) or 1.0
	return [(cint(r.weeks_after_arrival), flt(r.share_pct) / total) for r in mine]


def survival():
	"""The share of a consignment expected to reach the field.

	A provision, not a measurement, and it sits at zero until somebody sets it.
	The Eryngium book puts the real figure between nine and twelve per cent lost;
	allowing nothing for it is a decision rather than an oversight, so it is a
	field with a stated default rather than an assumption buried in arithmetic.
	"""
	loss = flt(frappe.db.get_single_value(
		"Summer Flower Settings", "propagation_loss_pct")) / 100.0
	return max(0.0, min(1.0, 1.0 - loss)) or 1.0


def _lift_batch(batch, waves, keep=1.0):
	"""Split one consignment into the lifts of plants it hands over."""
	qty = int(cint(batch["tc"]) * flt(keep or 1.0))
	out, running = [], 0
	for i, (weeks, share) in enumerate(waves):
		# The last lift takes the remainder, so the lifts add to the consignment
		# rather than to the nearest rounding error.
		plants = (qty - running if i == len(waves) - 1
		          else int(round(qty * share)))
		running += plants
		ready = add_days(getdate(batch["arrives"]), 7 * cint(weeks))
		out.append({
			"lift": i + 1, "weeks_after_arrival": cint(weeks),
			"share_pct": round(share * 100, 1),
			"plants": max(0, plants),
			"ready": str(ready), "ready_iso": iso(ready),
		})
	return [w for w in out if w["plants"]]
