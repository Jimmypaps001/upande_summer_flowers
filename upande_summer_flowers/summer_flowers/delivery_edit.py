"""Editing a plan's deliveries, and seeing what each change does to the demand.

The saved consignments are the one thing the plan's plantings are drawn from,
so every edit here changes a consignment and redraws the plan from them:
which supplier brings a delivery, the week it arrives, how much it brings,
an extra delivery, a delivery taken away, a half's total spread over its
deliveries. Each call answers with the plan as it now stands and its cover of
the demand before and after the change.
"""

import math

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers import plan_overview as po
from upande_summer_flowers.summer_flowers import supply_cycles as sc


def _plan(plan):
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("{0} is approved, so its deliveries cannot be changed. "
		               "Amend it to change them.").format(doc.name))
	if not doc.protocol:
		frappe.throw(_("{0} has no protocol version.").format(doc.name))
	return doc


def _effect(doc):
	"""The plan's cover of its demand, in the figures the page compares."""
	weeks = doc.get("plan_weeks") or []
	short = [w for w in weeks if cint(w.production_stems) < cint(w.demand_stems)]
	over = sum(max(0, cint(w.production_stems) - cint(w.demand_stems)) for w in weeks)
	return {
		"demand": cint(doc.total_demand_stems),
		"production": cint(doc.total_production_stems),
		"coverage_pct": round(flt(doc.coverage_pct), 1),
		"weeks_short": len(short),
		"stems_short": sum(cint(w.demand_stems) - cint(w.production_stems) for w in short),
		"stems_over": over,
		"plants": cint(doc.new_plants_required),
		"units": sum(cint(c.units) for c in (doc.get("plan_consignments") or [])),
		"deliveries": len(doc.get("plan_consignments") or []),
	}


def _supplier_terms(doc, v, supplier):
	"""(lead weeks, weeks from arrival to the first lift) for this supplier."""
	lead = 0
	for r in doc.get("plan_supply") or []:
		if r.supplier == supplier:
			lead = cint(r.lead_weeks)
			break
	if not lead:
		lead = cint(sc.supply_defaults(v).get("lead_weeks"))
	waves = po.waves_for(v, supplier) or [{"weeks_after": 0}]
	return lead, min(w["weeks_after"] for w in waves)


def _per_unit(v):
	from upande_summer_flowers.summer_flowers import root_line as rl

	try:
		return flt(rl.plants_per_unit_at_plant(v)) or 1.0
	except Exception:
		return 1.0


def _place(doc, v, row, supplier, arrives_monday, units):
	"""Write one consignment from what the user sees: supplier, arrival, units."""
	lead, first = _supplier_terms(doc, v, supplier)
	planted = add_days(arrives_monday, 7 * first)
	row.supplier = supplier
	row.lands_week = sc._iso(planted)
	row.lands_date = planted
	row.order_week = sc._iso(add_days(arrives_monday, -7 * lead))
	row.units = cint(units)
	row.plants = int(round(cint(units) * _per_unit(v)))


def _finish(doc, before, what):
	"""Redraw the plan from its consignments, save, and say what changed."""
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		rebuild_from_supply,
	)

	# Renumbered in the order they arrive, so "delivery 3" means the same
	# thing on the page and on the record.
	rows = sorted(doc.get("plan_consignments") or [],
	              key=lambda c: (getdate(c.lands_date) if c.lands_date else getdate(nowdate()),
	                             c.supplier or ""))
	for i, c in enumerate(rows, start=1):
		c.idx = i
		c.cycle_no = i
	doc.plan_consignments = rows
	# A changed delivery is a changed agreement: it is agreed again.
	doc.supply_confirmed = 0
	doc.supply_confirmed_on = None
	doc.supply_confirmed_by = None
	rebuild_from_supply(doc)
	doc.flags.ignore_validate_update_after_submit = True
	doc.save()
	after = _effect(doc)
	return {"done": what, "before": before, "after": after,
	        "overview": po.plan_overview(doc.name)}


def _monday(week_iso):
	at = sc._week_monday(week_iso)
	if not at:
		frappe.throw(_("{0} is not a week. Weeks look like 2028-W12.").format(week_iso))
	return at


def _row(doc, row):
	for c in doc.get("plan_consignments") or []:
		if c.name == row:
			return c
	frappe.throw(_("That delivery is not on {0}. Reload the page.").format(doc.name))


@frappe.whitelist()
def start_from_proposal(plan):
	"""Turn the proposed deliveries into the plan's own, so they can be edited."""
	doc = _plan(plan)
	if doc.get("plan_consignments"):
		return {"done": _("The plan already has its deliveries."),
		        "overview": po.plan_overview(doc.name)}
	picks = []
	for r in doc.get("plan_supply") or []:
		if r.supplier and r.supplier not in [p["supplier"] for p in picks]:
			picks.append({"supplier": r.supplier, "share_pct": flt(r.share_pct),
			              "cycle_count": cint(r.cycle_count), "lead_weeks": cint(r.lead_weeks)})
	if not picks:
		v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
		named = [h for h in (sc.supply_defaults(v).get("named_houses") or []) if h.get("supplier")]
		picks = [{"supplier": h["supplier"], "share_pct": flt(h.get("share_pct")),
		          "cycle_count": cint(h.get("cycle_weeks")), "lead_weeks": cint(h.get("lead_weeks"))}
		         for h in named]
	if not picks:
		frappe.throw(_("Name at least one supplier for this crop first."))
	before = _effect(doc)
	sc.save_supply(doc.name, picks, confirm=0)
	doc.reload()
	return {"done": _("The proposed deliveries are now the plan's."), "before": before,
	        "after": _effect(doc), "overview": po.plan_overview(doc.name)}


@frappe.whitelist()
def set_delivery(plan, row, supplier=None, arrives=None, units=None):
	"""Change one delivery: who brings it, the week it arrives, how much."""
	doc = _plan(plan)
	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	c = _row(doc, row)
	before = _effect(doc)
	_lead, first = _supplier_terms(doc, v, c.supplier)
	was_arrives = add_days(getdate(c.lands_date), -7 * first) if c.lands_date else None
	who = (supplier or "").strip() or c.supplier
	when = _monday(arrives) if arrives else was_arrives
	qty = cint(units) if units not in (None, "") else cint(c.units)
	if qty <= 0:
		frappe.throw(_("A delivery needs at least one unit. Remove it instead."))
	_place(doc, v, c, who, when, qty)
	return _finish(doc, before, _("Delivery changed: {0}, arriving {1}, {2:,} units.").format(
		who, sc._iso(when), qty))


@frappe.whitelist()
def add_delivery(plan, supplier, arrives, units):
	"""A delivery the rhythm did not propose."""
	doc = _plan(plan)
	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	if cint(units) <= 0:
		frappe.throw(_("A delivery needs at least one unit."))
	if not (supplier or "").strip():
		frappe.throw(_("Choose who brings it."))
	before = _effect(doc)
	c = doc.append("plan_consignments", {"by_hand": 1})
	_place(doc, v, c, supplier.strip(), _monday(arrives), cint(units))
	return _finish(doc, before, _("Delivery added: {0}, arriving {1}, {2:,} units.").format(
		supplier, arrives, cint(units)))


@frappe.whitelist()
def remove_delivery(plan, row):
	doc = _plan(plan)
	c = _row(doc, row)
	before = _effect(doc)
	doc.plan_consignments = [x for x in doc.plan_consignments if x.name != row]
	return _finish(doc, before, _("Delivery removed ({0}, {1:,} units).").format(
		c.supplier, cint(c.units)))


@frappe.whitelist()
def revise_half(plan, first_week, last_week, units):
	"""Set a half's total and spread it evenly over that half's deliveries.

	Each supplier takes its share of the half, split equally over its own
	deliveries arriving in it -- the same rule the proposal sizes a half by."""
	doc = _plan(plan)
	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	lo, hi = _monday(first_week), _monday(last_week)
	total = cint(units)
	if total <= 0:
		frappe.throw(_("A half needs at least one unit."))
	mine = []
	for c in doc.get("plan_consignments") or []:
		_lead, first = _supplier_terms(doc, v, c.supplier)
		arrives = add_days(getdate(c.lands_date), -7 * first) if c.lands_date else None
		if arrives and lo <= arrives <= hi:
			mine.append(c)
	if not mine:
		frappe.throw(_("No delivery arrives between {0} and {1}.").format(first_week, last_week))
	shares = {}
	for r in doc.get("plan_supply") or []:
		if r.supplier and r.supplier not in shares:
			shares[r.supplier] = flt(r.share_pct)
	houses = sorted({c.supplier for c in mine})
	weight = {h: (shares.get(h) or 0.0) for h in houses}
	if sum(weight.values()) <= 0:
		weight = {h: 1.0 for h in houses}
	scale = sum(weight.values())
	before = _effect(doc)
	per_unit = _per_unit(v)
	for h in houses:
		rows = [c for c in mine if c.supplier == h]
		each = int(math.ceil(total * weight[h] / scale / len(rows) - 1e-9))
		for c in rows:
			c.units = each
			c.plants = int(round(each * per_unit))
	return _finish(doc, before, _("Half revised to {0:,} units over {1} deliveries.").format(
		total, len(mine)))


@frappe.whitelist()
def suppliers(plan):
	"""Who can be chosen to bring a delivery: the plan's, then any other house."""
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	names = []
	for r in doc.get("plan_supply") or []:
		if r.supplier and r.supplier not in names:
			names.append(r.supplier)
	for c in doc.get("plan_consignments") or []:
		if c.supplier and c.supplier not in names:
			names.append(c.supplier)
	try:
		more = sc.suppliers_for() or {}
		for s in (more.get("suppliers") or []) + (more.get("others") or []):
			n = s.get("supplier") if isinstance(s, dict) else s
			if n and n not in names:
				names.append(n)
	except Exception:
		pass
	return names
