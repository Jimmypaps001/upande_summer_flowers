# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The troughs the plan asked for, against the troughs that were actually set.

The planner chooses troughs: so many of this size, set in these weeks, each
paying back on the protocol's lift dates. That was a promise nothing could be
held to -- the plan said six troughs in January and the register said nothing
at all, because a trough's occupancy was inferred from the plantings it was
assumed to serve rather than recorded when somebody filled it.

A Trough Setting is that record. This sets the two side by side: what was
planned, what was set, what came off, and where the two part company. It reads
both and writes neither.

A lift is also the moment plants leave the unit for the field, so a recorded
lift is an actual the production plan is entitled to see. feed_actuals turns
each one into a Summer Flower Propagation Actual, which is the record the
variance engine already reads -- rather than asking the propagator to type the
same number into a second place.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

SETTING = "Trough Setting"
ACTUAL = "Summer Flower Propagation Actual"


def _have_settings():
	return frappe.db.table_exists(SETTING)


def settings_for(production_plan=None, unit=None, variety=None, batch=None):
	"""Every setting that answers this plan, newest last."""
	if not _have_settings():
		return []
	filters = {"docstatus": ["<", 2]}
	for k, v in (("production_plan", production_plan), ("propagation_unit", unit),
	             ("variety", variety), ("propagation_batch", batch)):
		if v:
			filters[k] = v
	return frappe.get_all(
		SETTING, filters=filters, order_by="set_date asc, name asc",
		fields=["name", "trough", "propagation_unit", "variety", "form",
		        "set_date", "plants", "units", "capacity_plants", "status",
		        "plants_lifted", "plants_remaining", "freed_on",
		        "propagation_batch", "crop_protocol_version"])


def lifts_of(settings):
	"""The lift rows of these settings, flattened, with their parent."""
	names = [s["name"] for s in settings]
	if not names:
		return []
	return frappe.get_all(
		"Propagation Trough Lift",
		filters={"parent": ["in", names], "parenttype": SETTING},
		fields=["parent", "lift_no", "due_date", "plants_due", "lift_date",
		        "plants_taken", "variance", "destination"],
		order_by="parent asc, lift_no asc")


@frappe.whitelist()
def against_plan(production_plan):
	"""Plan versus register, trough for trough.

	The plan's troughs come from the card -- it works them out rather than
	storing them -- so they are asked for here the same way the card asks, and
	the two cannot drift apart.
	"""
	from upande_summer_flowers.summer_flowers import root_line

	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	card = root_line.arrival_plan(plan) or {}
	planned = (card.get("troughs") or {})
	want = planned.get("settings") or []

	actual = settings_for(production_plan=production_plan) or settings_for(
		variety=plan.variety)
	rows = lifts_of(actual)
	by_set = {}
	for r in rows:
		by_set.setdefault(r["parent"], []).append(r)

	# Matched on the week a trough was set, because that is the decision the
	# plan made. A trough set a fortnight late is the same trough, late -- not
	# a different one -- and reporting it as missing plus unexpected would hide
	# the only fact worth knowing about it.
	done = []
	for a in actual:
		best, gap = None, None
		for i, w in enumerate(want):
			if i in done or not a.get("set_date"):
				continue
			d = abs((getdate(a["set_date"]) - getdate(w["set_week"])).days)
			if gap is None or d < gap:
				best, gap = i, d
		a["_matched"] = best
        # Three weeks either side: beyond that it answers a different decision.
		if best is not None and gap is not None and gap <= 21:
			done.append(best)
		else:
			a["_matched"] = None

	return {
		"plan": plan.name, "variety": plan.variety, "farm": plan.farm,
		"planned": {
			"unit": planned.get("unit"),
			"troughs": cint(planned.get("used")),
			"owned": cint(planned.get("owned")),
			"roots": cint(planned.get("roots")),
			"plants": cint(planned.get("plants")),
			"settings": want,
		},
		"actual": {
			"troughs": len(actual),
			"plants": sum(cint(a["plants"]) for a in actual),
			"units": sum(cint(a["units"]) for a in actual),
			"lifted": sum(cint(a["plants_lifted"]) for a in actual),
			"standing": sum(1 for a in actual
			                if a["status"] in ("Standing", "Partly Lifted")),
			"settings": [dict(a, lifts=by_set.get(a["name"], [])) for a in actual],
		},
		"gap": {
			"troughs": len(actual) - cint(planned.get("used")),
			"plants": sum(cint(a["plants"]) for a in actual)
			          - cint(planned.get("plants")),
			"unplanned": [a["name"] for a in actual if a["_matched"] is None],
			"not_set": [w["set_week"] for i, w in enumerate(want) if i not in done],
		},
		"note": _note(planned, actual, done, want),
	}


def _note(planned, actual, done, want):
	"""One sentence a grower can act on, or nothing."""
	if not want and not actual:
		return None
	if not actual:
		return _("The plan asks for {0} troughs and none has been set yet.").format(
			cint(planned.get("used")))
	short = len(want) - len(done)
	extra = sum(1 for a in actual if a["_matched"] is None)
	bits = []
	if short > 0:
		bits.append(_("{0} of the {1} troughs the plan asked for have not been set")
		            .format(short, len(want)))
	if extra:
		bits.append(_("{0} were set that the plan did not ask for").format(extra))
	if not bits:
		return _("Every trough the plan asked for has been set.")
	return "; ".join(bits).capitalize() + "."


@frappe.whitelist()
def feed_actuals(trough_setting):
	"""Turn this setting's recorded lifts into propagation actuals.

	A lift is plants leaving the unit, which is exactly the event the plan's
	variance engine reads. Writing it here rather than asking the propagator to
	type it twice is the difference between a plan that knows what happened and
	one that knows what was typed into it.

	Idempotent: a lift already fed is skipped, so running it again after a
	correction adds the correction and nothing else.
	"""
	doc = frappe.get_doc(SETTING, trough_setting)
	if doc.docstatus == 2:
		frappe.throw(_("{0} is cancelled.").format(doc.name))
	plan = frappe.db.get_value(
		"Summer Flower Propagation Plan",
		{"production_plan": doc.production_plan, "docstatus": ["<", 2]}, "name") \
		if doc.production_plan else None
	made, skipped = [], 0
	for r in doc.lifts:
		if not r.lift_date or not cint(r.plants_taken):
			continue
		ref = "%s#%s" % (doc.name, r.lift_no)
		if frappe.db.exists(ACTUAL, {"reference": ref, "docstatus": ["<", 2]}):
			skipped += 1
			continue
		a = frappe.get_doc({
			"doctype": ACTUAL,
			"propagation_plan": plan,
			"variety": doc.variety,
			"farm": frappe.db.get_value("Block", r.destination, "farm")
			        if r.destination else None,
			"event": "Plants To Farm",
			"actual_date": r.lift_date,
			"against_week": r.due_date,
			"units": cint(r.plants_taken),
			"plants": cint(r.plants_taken),
			"form": "Plants",
			"warehouse": doc.propagation_unit,
			"recorded_from": "Typed In",
			"reference": ref,
			"remarks": _("Lift {0} off {1} in {2}.").format(
				r.lift_no, doc.trough, doc.propagation_unit or _("the unit")),
		})
		a.flags.ignore_permissions = True
		a.insert()
		a.submit()
		made.append(a.name)
	return {"created": made, "already_there": skipped,
	        "note": _("{0} lift(s) recorded against the plan; {1} were already there."
	                  ).format(len(made), skipped)}
