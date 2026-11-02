# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""What the unit did, written once, where the plan reads it.

The propagation unit records real work -- a delivery checked in, plants dying
on the bench, seedlings going out to the farm, a trough lifted -- and none of
it reached the production plan. The plan's variance engine reads two sources:
the stock ledger, and Summer Flower Propagation Actual. Anything that moved
stock was already visible; everything else happened in a different app and the
plan never heard about it.

So this is the bridge, and it follows the rule propagation_actuals already
states rather than inventing a second one: **where the ledger carries an event,
the ledger wins.** A dispatch that wrote a Stock Entry is already counted, and
writing a typed record beside it would be a second opinion that loses -- the
resolution is per event type across the whole season, so one stray typed
record cannot even be half-counted. It is skipped, and said out loud.

What the ledger never carries is loss. Plants die on a bench without moving
anywhere, so mortality has no voucher and no movement, and until now the plan
could not see the one thing most likely to make it wrong.

Every write is keyed to the record it came from, so running it again after a
correction adds the correction and changes nothing else.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate

ACTUAL = "Summer Flower Propagation Actual"


def _plan_for(variety, farm=None):
	"""The open propagation plan this event belongs to, if there is one."""
	if not variety:
		return None
	filters = {"variety": variety, "docstatus": ["<", 2]}
	if farm:
		filters["farm"] = farm
	return frappe.db.get_value("Summer Flower Propagation Plan", filters, "name") \
		or frappe.db.get_value("Summer Flower Propagation Plan",
		                       {"variety": variety, "docstatus": ["<", 2]}, "name")


def _write(reference, **kw):
	"""One actual, written once. Returns its name, or None if it was there."""
	if frappe.db.exists(ACTUAL, {"reference": reference, "docstatus": ["<", 2]}):
		return None
	doc = frappe.get_doc(dict(doctype=ACTUAL, reference=reference,
	                          recorded_from="Typed In", **kw))
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()
	return doc.name


def _result(name, skipped_because=None):
	if skipped_because:
		return {"actual": None, "skipped": skipped_because}
	return {"actual": name, "skipped": None if name else _("already recorded")}


# ------------------------------------------------------------------- mortality
def from_mortality(log):
	"""Plants that died on the bench.

	The one event the ledger can never carry: nothing moves when a tray fails,
	so without this the plan sees a batch arrive and a smaller batch leave and
	has no idea which part was loss and which was still standing.
	"""
	d = frappe.get_doc("Daily Propagation Log", log)
	dead = cint(d.get("mortality_today")) or cint(d.get("affected_qty"))
	if dead <= 0:
		return _result(None, _("no mortality on this log"))
	batch = frappe.db.get_value(
		"Propagation Batch", d.propagation_batch,
		["variety", "propagation_unit"], as_dict=True) if d.propagation_batch else None
	variety = (batch or {}).get("variety") or d.get("rose_variety")
	if not variety:
		return _result(None, _("the log names no variety"))
	return _result(_write(
		"%s#loss" % d.name,
		propagation_plan=_plan_for(variety),
		variety=variety, event="Loss", actual_date=getdate(d.log_date),
		units=dead, plants=dead, form="Plants",
		warehouse=(batch or {}).get("propagation_unit") or d.get("greenhouse"),
		reason=d.get("observations") or d.get("disease"),
		remarks=_("Mortality logged on {0}{1}.").format(
			d.name, _(" for batch %s") % d.propagation_batch
			if d.propagation_batch else "")))


# ----------------------------------------------------------------- consignment
def from_consignment(consignment):
	"""A delivery checked in. Skipped where a purchase receipt already booked it."""
	d = frappe.get_doc("Plant Consignment", consignment)
	if d.docstatus != 1:
		return _result(None, _("not submitted"))
	if d.get("purchase_receipt"):
		return _result(None, _("{0} booked it on the ledger, which the plan already "
		                       "reads").format(d.purchase_receipt))
	qty = cint(d.get("qty_accepted")) or cint(d.get("qty_received"))
	if qty <= 0:
		return _result(None, _("nothing was accepted"))
	form = d.get("form") or "Tissue Culture"
	return _result(_write(
		"%s#in" % d.name,
		propagation_plan=d.get("propagation_plan") or _plan_for(d.variety),
		variety=d.variety,
		event="Roots Withdrawn" if form == "Roots" else "TC Received",
		actual_date=getdate(d.arrival_date), units=qty, plants=0, form=form,
		warehouse=d.get("warehouse"), supplier=d.get("supplier"),
		remarks=_("{0} received from {1}, {2} accepted of {3}.").format(
			d.name, d.supplier or _("a supplier"), f"{qty:,}",
			f"{cint(d.qty_received):,}")))


# -------------------------------------------------------------------- dispatch
def from_dispatch(dispatch):
	"""Seedlings going to the farm. Skipped where a stock entry already moved them."""
	d = frappe.get_doc("Seedling Dispatch", dispatch)
	if d.docstatus != 1:
		return _result(None, _("not submitted"))
	if d.get("stock_entry"):
		return _result(None, _("{0} moved them on the ledger, which the plan "
		                       "already reads").format(d.stock_entry))
	qty = cint(d.get("qty_dispatched"))
	if qty <= 0:
		return _result(None, _("nothing was dispatched"))
	return _result(_write(
		"%s#out" % d.name,
		propagation_plan=_plan_for(d.variety, d.get("farm")),
		variety=d.variety, farm=d.get("farm"), event="Plants To Farm",
		actual_date=getdate(d.dispatch_date), units=0, plants=qty, form="Plants",
		warehouse=d.get("source_warehouse"),
		remarks=_("{0}: {1} plants to {2} as a paper dispatch -- no stock entry "
		          "was raised.").format(d.name, f"{qty:,}",
		                                d.get("farm") or d.get("target_greenhouse")
		                                or _("the farm"))))


# ------------------------------------------------------------------- the hook
# Called from upande_propagation after each operation. Never fatal: the unit's
# own record is the thing that must be written, and a plan that misses one
# event is better than a dispatch that refuses to save.
EVENTS = {"Daily Propagation Log": from_mortality,
          "Plant Consignment": from_consignment,
          "Seedling Dispatch": from_dispatch}


def feed(doctype, name):
	fn = EVENTS.get(doctype)
	if not fn:
		return None
	try:
		return fn(name)
	except Exception:
		frappe.log_error(frappe.get_traceback(),
		                 "Could not feed %s %s to the production plan" % (doctype, name))
		return None
