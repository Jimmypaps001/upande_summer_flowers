# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Plant material arriving on a Purchase Receipt or a Purchase Invoice.

There is no Plants Receipt doctype and there should not be one. Plants are
bought, so they arrive the way everything bought arrives:

    Purchase Order -> Purchase Receipt (or a Purchase Invoice with
    update_stock) -> Quality Inspection -> Stock Ledger / Batch

and ERPNext already carries every field the delivery needs -- received_qty,
qty as the accepted quantity, rejected_qty, rejected_warehouse,
custom_rejection_reason, batch_no, quality_inspection per line. Item's
inspection_required_before_purchase plus quality_inspection_template is what
makes the inspection appear without anyone choosing one; setup/plant_quality.py
switches that on for plant items.

This module adds only the three things ERPNext does not have:

1.  An inspection on an invoice that does not update stock.
    erpnext/controllers/stock_controller.py:1508 returns early from
    validate_inspection when a Purchase Invoice has update_stock = 0, so the
    inspection requirement never fires on it. Every one of the 3,352 submitted
    invoices on this site has update_stock = 0, so on the invoice route -- which
    is how plants often arrive -- native enforcement is silently absent.

2.  Agronomic context on the line. A purchase line knows the item and the
    supplier; it does not know which planting the plants are for, so nothing
    connects a delivery to the plan it was ordered against.

3.  The plan comparison. The planting was approved for a plant count; the
    supplier delivers another. That gap is the thing production planning needs
    to see, and it has to survive a partial delivery, a second delivery against
    the same planting, and a cancellation.

Quantities are read back from submitted documents rather than accumulated,
so a cancel, an amend or a return corrects the planting instead of drifting.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

RECEIPT_DOCTYPES = ("Purchase Receipt", "Purchase Invoice")
DEFAULT_PLANT_GROUPS = ("Summer Flowers",)
DEFAULT_TOLERANCE_PCT = 5.0


# --------------------------------------------------------------- what is a plant
def plant_item_groups():
	raw = frappe.db.get_single_value("Summer Flower Settings", "plant_item_groups")
	groups = [g.strip() for g in (raw or "").split(",") if g.strip()]
	return tuple(groups) or DEFAULT_PLANT_GROUPS


def is_plant_line(row):
	if not row.get("item_code"):
		return False
	group = row.get("item_group") or frappe.get_cached_value(
		"Item", row.item_code, "item_group")
	return group in plant_item_groups()


def plant_lines(doc):
	return [r for r in (doc.get("items") or []) if is_plant_line(r)]


# ------------------------------------------------------------------ line context
def set_line_context(doc, method=None):
	"""Derive the planting context onto each plant line, on every save.

	Read-only on the form: the only thing anyone picks is the planting, and
	everything else follows from it, so a line cannot claim a planned quantity
	that the planting does not have.
	"""
	for row in plant_lines(doc):
		cal = row.get("custom_planting_calendar")
		if not cal:
			row.custom_block = None
			row.custom_planned_plants = 0
			row.custom_plant_variance = 0
			row.custom_days_before_planting = 0
			continue

		plan = frappe.db.get_value(
			"Planting Calendar", cal,
			["block", "variety", "plants", "planting_date", "farm"], as_dict=True)
		if not plan:
			continue

		if plan.variety and row.item_code != plan.variety:
			frappe.throw(_(
				"Row #{0}: planting {1} is for variety {2}, not {3}."
			).format(row.idx, cal, frappe.bold(plan.variety),
			         frappe.bold(row.item_code)))

		row.custom_block = plan.block
		row.custom_planned_plants = cint(plan.plants)
		row.custom_plant_variance = cint(row.get("qty")) - cint(plan.plants)
		row.custom_days_before_planting = _days_before(doc, plan.planting_date)


def _days_before(doc, planting_date):
	"""Days between the receipt and the planting date. Negative means late."""
	if not planting_date:
		return 0
	received = getdate(doc.get("posting_date") or frappe.utils.nowdate())
	return (getdate(planting_date) - received).days


# ------------------------------------------------- inspection on the invoice route
def require_inspection(doc, method=None):
	"""Close the gap ERPNext leaves on an invoice that does not update stock.

	Deliberately narrow: only plant lines, only when the item itself asks for an
	inspection, and only on the document that native validation skips. On a
	Purchase Receipt, or an invoice that does update stock, ERPNext has already
	run this and running it again would only duplicate the message.
	"""
	if doc.doctype != "Purchase Invoice" or doc.get("update_stock"):
		return
	if doc.get("is_return"):
		return
	if not cint(frappe.db.get_single_value(
			"Summer Flower Settings", "require_inspection_on_invoice")):
		return

	not_submitted = frappe.db.get_single_value(
		"Stock Settings", "action_if_quality_inspection_is_not_submitted")
	rejected_action = frappe.db.get_single_value(
		"Stock Settings", "action_if_quality_inspection_is_rejected")

	for row in plant_lines(doc):
		if not frappe.get_cached_value(
				"Item", row.item_code, "inspection_required_before_purchase"):
			continue

		if not row.get("quality_inspection"):
			frappe.throw(
				_("Row #{0}: plant material needs a Quality Inspection. "
				  "Item {1} is inspected before purchase, and this invoice does not "
				  "update stock, so nothing else will ask for one.").format(
					row.idx, frappe.bold(row.item_code)),
				title=_("Inspection Required"))

		status, docstatus = frappe.db.get_value(
			"Quality Inspection", row.quality_inspection, ["status", "docstatus"])
		link = frappe.utils.get_link_to_form(
			"Quality Inspection", row.quality_inspection)

		if docstatus != 1:
			msg = _("Row #{0}: Quality Inspection {1} is not submitted.").format(
				row.idx, link)
			if not_submitted == "Stop":
				frappe.throw(msg, title=_("Inspection Submission"))
			frappe.msgprint(msg, alert=True, indicator="orange")

		if status == "Rejected":
			msg = _("Row #{0}: Quality Inspection {1} was rejected.").format(
				row.idx, link)
			if rejected_action == "Stop":
				frappe.throw(msg, title=_("Inspection Rejected"))
			frappe.msgprint(msg, alert=True, indicator="orange")


# ------------------------------------------------------------- back to the plan
def refresh_plantings(doc, method=None):
	"""Recompute every planting this document touches, on submit and on cancel."""
	names = {r.get("custom_planting_calendar") for r in plant_lines(doc)}
	for name in sorted(n for n in names if n):
		refresh_planting(name, notify=(method == "on_submit"))


def _received_lines(planting):
	"""Every submitted plant line pointing at this planting, both routes.

	Returns rows already netted for returns: a return document carries negative
	quantities in ERPNext, so summing docstatus 1 across both is correct.
	"""
	rows = []
	for dt in RECEIPT_DOCTYPES:
		child = dt + " Item"
		rows += frappe.db.sql("""
			select c.qty, c.rejected_qty, c.received_qty, p.posting_date,
			       c.parent, c.parenttype
			from `tab{child}` c
			join `tab{parent}` p on p.name = c.parent
			where c.custom_planting_calendar = %(planting)s
			  and p.docstatus = 1
		""".format(child=child, parent=dt), {"planting": planting}, as_dict=True)
	return rows


@frappe.whitelist()
def refresh_planting(planting, notify=False):
	"""Write the received figures onto the planting. Idempotent by construction."""
	if not frappe.db.exists("Planting Calendar", planting):
		return

	plan = frappe.db.get_value(
		"Planting Calendar", planting,
		["plants", "planting_date", "farm", "variety", "delivery_status"],
		as_dict=True)
	rows = _received_lines(planting)

	accepted = sum(flt(r.qty) for r in rows)
	rejected = sum(flt(r.rejected_qty) for r in rows)
	dates = sorted(getdate(r.posting_date) for r in rows if r.posting_date)
	planned = cint(plan.plants)
	variance = cint(accepted) - planned

	tolerance = flt(frappe.db.get_single_value(
		"Summer Flower Settings", "plant_delivery_tolerance_pct")) or DEFAULT_TOLERANCE_PCT
	variance_pct = (variance / planned * 100.0) if planned else 0.0

	if not rows or accepted <= 0:
		status = "Nothing Received"
	elif abs(variance_pct) <= tolerance:
		status = "Fully Received"
	elif variance > 0:
		status = "Over Received"
	elif accepted < planned:
		# Short of the plan, but the order may simply not be finished yet.
		status = "Short Received" if _order_closed(planting) else "Partially Received"
	else:
		status = "Fully Received"

	last = dates[-1] if dates else None
	frappe.db.set_value("Planting Calendar", planting, {
		"plants_received": cint(accepted),
		"plants_rejected": cint(rejected),
		"plants_variance": variance,
		"variance_pct": variance_pct,
		"delivery_status": status,
		"first_receipt_date": dates[0] if dates else None,
		"last_receipt_date": last,
		"days_before_planting": (
			(getdate(plan.planting_date) - last).days
			if last and plan.planting_date else 0),
	}, update_modified=False)

	if notify and status in ("Short Received", "Over Received"):
		_alert(planting, plan, cint(accepted), planned, variance, variance_pct, status)

	return status


def _order_closed(planting):
	"""Is there still an open purchase order line for this planting?

	Short of the plan with the order still open is a partial delivery, not a
	shortfall, and alerting on it would cry wolf on every split delivery.
	"""
	pending = frappe.db.sql("""
		select 1
		from `tabPurchase Order Item` poi
		join `tabPurchase Order` po on po.name = poi.parent
		where poi.custom_planting_calendar = %s
		  and po.docstatus = 1 and po.status not in ('Closed', 'Completed')
		  and poi.received_qty < poi.qty
		limit 1
	""", planting)
	return not pending


def _alert(planting, plan, accepted, planned, variance, variance_pct, status):
	"""Tell the farm, once, with the numbers and the decision that follows."""
	role = frappe.db.get_single_value(
		"Summer Flower Settings", "plant_delivery_notify_role") or "Farm Manager"
	recipients = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
	recipients = [
		u for u in set(recipients)
		if frappe.db.get_value("User", u, "enabled")
	]
	if not recipients:
		return

	short = "short by" if variance < 0 else "over by"
	subject = _("{0}: plant delivery {1} {2} plants").format(
		planting, short, abs(variance))
	message = _(
		"Planting {0} ({1}, {2}) was planned for {3} plants and {4} have been "
		"accepted -- {5} {6} ({7}%).<br><br>"
		"Either revise the planting to the delivered quantity, which keeps the "
		"approved figure on the record, or chase the balance with the supplier."
	).format(
		frappe.utils.get_link_to_form("Planting Calendar", planting),
		plan.variety or "", plan.farm or "", planned, accepted,
		short, abs(variance), round(variance_pct, 1))

	for user in recipients:
		frappe.get_doc({
			"doctype": "Notification Log",
			"for_user": user,
			"type": "Alert",
			"document_type": "Planting Calendar",
			"document_name": planting,
			"subject": subject,
			"email_content": message,
		}).insert(ignore_permissions=True)


# --------------------------------------------------------------- plan revision
@frappe.whitelist()
def revise_to_delivered(planting, reason=None):
	"""Revise a planting down to what actually arrived, keeping the original.

	plants is derived on Planting Calendar (beds x plants_per_bed) and cannot be
	written directly -- validate would recompute it -- so the revision moves the
	bed count, which is the thing a farm actually changes when fewer plants turn
	up, and the approved figures are snapshotted first.
	"""
	doc = frappe.get_doc("Planting Calendar", planting)
	received = cint(doc.plants_received)
	if received <= 0:
		frappe.throw(_("Nothing has been received against {0} yet.").format(planting))

	per_bed = (cint(doc.plants) / cint(doc.beds)) if cint(doc.beds) else 0
	if not per_bed:
		frappe.throw(_("{0} has no bed count to revise.").format(planting))

	beds = max(1, int(received // per_bed))
	if beds == cint(doc.beds):
		frappe.msgprint(_("{0} already matches the delivery at {1} bed(s).").format(
			planting, beds))
		return doc.beds

	if not doc.beds_before_revision:
		doc.beds_before_revision = doc.beds
		doc.plants_before_revision = doc.plants

	was = doc.beds
	doc.beds = beds
	doc.revision_reason = reason or _(
		"Revised to the delivered quantity of {0} plants.").format(received)
	doc.revised_on = frappe.utils.now_datetime()
	doc.revised_by = frappe.session.user
	doc.flags.ignore_permissions = True
	doc.save()

	frappe.msgprint(_("{0}: {1} bed(s) revised to {2}, {3} plants approved kept "
	                  "on the record.").format(
		planting, was, beds, doc.plants_before_revision))
	return beds
