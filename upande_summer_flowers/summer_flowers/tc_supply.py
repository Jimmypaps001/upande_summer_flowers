# Copyright (c) 2026, James Kiruga and contributors
"""Tissue culture arrives in batches, from more than one supplier."""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

from upande_summer_flowers.summer_flowers.root_line import _monday, iso


def batch_plan(plan, allocations, lead_weeks=None, to_ground_weeks=None,
               tc_target=None):
	"""Every supplier's batches, sized by the demand each one serves."""
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
		run["_waves"] = handover_waves("Tissue Culture", a.get("supplier"), ground,
		                               version=plan.protocol)
		run["lifts_per_consignment"] = len(run["_waves"])
		grand += run["tc_total"]
		out.append(run)

	# What the propagation unit can take is a harder constraint than anything a
	keep = survival("Tissue Culture", version=plan.protocol)
	limits = capacity_limits()
	# Material occupies the unit until the LAST lift comes off it. A unit that
	limits["hold_weeks"] = max(
		[cint(limits.get("hold_weeks")), ground]
		+ [w for run in out for w, _s in run["_waves"]])
	fitted = _fit_intake(out, limits["weekly_intake"], lead)
	for run in out:
		_renumber(run)
		for b in run["batches"]:
			b["lifts"] = _lift_batch(b, run["_waves"], keep)
	# Each consignment's lifts add up to that consignment. Across nineteen of
	_settle_rounding(out, keep)
	# Measured against the plants TISSUE CULTURE is responsible for, not against
	handovers = _cover(out, _tc_week_need(plan, need, tc_target, keep))
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
	first = _monday(alloc.get("first_week")
	                or add_days(min(need), -7 * land_lag))

	# Send weeks: start at the first, step by the cycle, and stop once a batch
	weeks, w = [], first
	while add_days(w, 7 * land_lag) <= last_land:
		weeks.append(w)
		w = add_days(w, 7 * cycle)
	if not weeks:
		weeks = [first]

	# How many consignments the lab is to send, if the planner has said. The
	cap = cint(alloc.get("batches"))
	if cap and cap < len(weeks):
		step = (len(weeks) - 1) / float(cap - 1) if cap > 1 else 0
		weeks = [weeks[int(round(i * step))] for i in range(cap)]

	# What each batch feeds: the planting weeks from its landing until the next
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
		qty = (tc - running if i == len(weeks) - 1
		       else int(round(tc * served[i] / float(total_served))))
		qty = max(0, qty)
		running += qty
		# Two different weeks, which were one: the material ARRIVES at the
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


def _settle_rounding(out, keep):
	"""Make the lifts of every consignment add to what the whole order yields."""
	want = int(round(sum(cint(b["tc"]) for run in out
	                     for b in run["batches"]) * flt(keep or 1.0)))
	lifts = [w for run in out for b in run["batches"] for w in (b.get("lifts") or [])]
	if not lifts:
		return
	got = sum(cint(w["plants"]) for w in lifts)
	if got == want:
		return
	last = max(lifts, key=lambda w: str(w.get("ready") or ""))
	last["plants"] = max(0, cint(last["plants"]) + (want - got))


def _tc_week_need(plan, need, tc_target, keep):
	"""The share of each planting week that tissue culture has to cover."""
	from upande_summer_flowers.summer_flowers.root_line import _apportion

	plants = int(cint(tc_target) * flt(keep or 1.0))
	if not plants:
		return dict(need)
	weeks = {k: {"plants": v} for k, v in need.items()}
	return _apportion(weeks, 1.0, plants)


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
	"""The labs this site can actually order from."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	f = {}
	if search:
		f["name"] = ["like", "%%%s%%" % search]
	# Suppliers awaiting approval are offered too, and named as pending. This
	fields = ["name", "disabled"]
	if frappe.db.has_column("Supplier", "workflow_state"):
		fields.append("workflow_state")
	rows = frappe.get_all(
		"Supplier", filters=f,
		fields=fields,
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

def propagation_units(form="Tissue Culture"):
	"""The warehouses plants are raised in, with what each one can take."""
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
	"""The pool's ceilings. Zero means nobody has said, and nothing is applied."""
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
	"""Nothing arrives in a week the unit cannot set down."""
	report = {"moved": 0, "unplaceable": 0, "weeks": 0}
	if not cap:
		return report
	for _ in range(500):
		intake = _intake(suppliers)
		over = [w for w in sorted(intake) if intake[w] > cap]
		if not over:
			break
		# Latest first: moving a late week's excess back opens nothing behind
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
	"""How far into the season each lift takes us, counted cumulatively."""
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
		# The week the field wants this lift is not the week it is ready:
		to_farm = next((str(d) for d, _q in wanted if str(d) >= str(ready)), None)
		w["to_farm"] = to_farm
		timeline.append({
			"supplier": supplier, "batch": b["batch"], "lift": w["lift"],
			"week": w["ready"], "week_iso": w["ready_iso"],
			"plants": w["plants"], "share_pct": w["share_pct"],
			"weeks_after_arrival": w["weeks_after_arrival"],
			"plants_served": w["plants_served"], "serves_to": w["serves_to"],
			# Where this lift came from, so the whole life of a consignment reads
			"ordered": b["week"], "ordered_iso": b["week_iso"],
			"arrives": b["arrives"], "arrives_iso": b["arrives_iso"],
			"consignment_tc": b["tc"],
			"to_farm": to_farm,
			"to_farm_iso": iso(to_farm) if to_farm else None,
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

def handover_waves(form="Tissue Culture", supplier=None, default_weeks=0,
                   version=None):
	"""[(weeks after it arrives, share of the consignment), ...]."""
	# The protocol's own profile first, where it states one. How a crop is
	if version and not supplier:
		from upande_summer_flowers.summer_flowers import crop_protocol as cp

		try:
			doc = (version if hasattr(version, "get")
			       else frappe.get_cached_doc("Crop Protocol Version", version))
			mine = cp.protocol_lifts(doc, form)
			if mine:
				return mine
		except Exception:
			frappe.clear_last_message()

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
	# Taken as stated. Settings refuses a profile that does not add to a
	total = sum(flt(r.share_pct) for r in mine) or 1.0
	scale = 100.0 if abs(total - 100.0) <= 0.5 else total
	return [(cint(r.weeks_after_arrival), flt(r.share_pct) / scale) for r in mine]


def handover_for(form="Tissue Culture", supplier=None, default_weeks=0,
                 version=None):
	"""How THIS house hands a consignment over, in lifts.

	Its own profile where Settings states one, and the crop's own where it does
	not -- a house that says nothing about its lifts is taken to hand the crop
	over the way the crop behaves, not to drop the whole order in a week."""
	if supplier:
		rows = frappe.get_all(
			"Summer Flower Handover Wave",
			filters={"parent": "Summer Flower Settings", "form": form,
			         "source": supplier},
			fields=["lift_no", "weeks_after_arrival", "share_pct"],
			order_by="lift_no asc")
		if rows:
			total = sum(flt(r.share_pct) for r in rows) or 1.0
			scale = 100.0 if abs(total - 100.0) <= 0.5 else total
			return [(cint(r.weeks_after_arrival), flt(r.share_pct) / scale)
			        for r in rows]
	return handover_waves(form, None, cint(default_weeks), version)


def survival(form="Tissue Culture", version=None):
	"""The share of a consignment of `form` expected to reach the field."""
	from upande_summer_flowers.summer_flowers.route_ways import route_loss

	loss = flt(route_loss(version, form)) if version else 0.0
	return max(0.0, min(1.0, 1.0 - loss / 100.0)) or 1.0


def _lift_batch(batch, waves, keep=1.0):
	"""Split one consignment into the lifts of plants it hands over."""
	qty = int(cint(batch["tc"]) * flt(keep or 1.0))
	out, running = [], 0
	for i, (weeks, share) in enumerate(waves):
		# The last lift takes the remainder, so the lifts add to the consignment
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
