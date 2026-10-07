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
		run = _one_supplier(a, need, land_lag)
		grand += run["tc_total"]
		out.append(run)

	wanted = sum(need.values())
	target = cint(tc_target) if cint(tc_target) else wanted
	return {
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


def _one_supplier(alloc, need, land_lag):
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
		batches.append({
			"batch": i + 1, "week": str(sw), "week_iso": iso(sw),
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
