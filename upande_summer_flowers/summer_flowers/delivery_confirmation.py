# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""What the supplier has agreed to send, week by week.

A plan is a perfect thing: it asks for exactly the material each planting week
needs, on the day it needs it, and it has never once been told whether anybody
can supply that. The order went out and the farm found out what was coming when
it arrived.

So the order is drafted and left as a draft, and this is where it is agreed. One
row a delivery, two figures to change -- how many and which week -- and
submitting the order is the act of confirming the schedule. After that the farm
is planning against what a supplier said they would send rather than against
what the farm wished for.

Nothing here is a new record. The Purchase Order already IS the delivery
schedule: a line per delivery, each with its own required-by date, and ERPNext
tracks what has been received against each. A second document holding the same
promise could only disagree with it.

Whoever opens it may fill it in. A lab keying its own schedule is the point; a
buyer keying it off an email is the same fact arriving another way. Every save
is versioned, so who said what is answerable either way.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers.planning import season_year


def _plan_orders(procurement_plan):
	"""The orders drafted for this plan.

	Found through the requests the plan raised, not through the purchase
	allocation. The allocation records WHO was chosen to sell, and a plan can
	have raised its orders without anybody having been chosen -- which is the
	normal state of a plan between approval and the first allocation, and it is
	exactly when somebody wants to see the schedule. Reading the link off the
	allocation found nothing for those plans and the tab showed no orders at
	all, for orders that plainly existed.
	"""
	mrs = {r.source_doc for r in frappe.get_all(
		"Planting Material Requirement",
		filters={"parent": procurement_plan,
		         "parenttype": "Summer Flower Procurement Plan",
		         "source_doctype": "Material Request",
		         "source_doc": ["is", "set"]},
		fields=["source_doc"])}
	if not mrs:
		return []
	stage = frappe.db.get_value(
		"Planting Material Requirement",
		{"parent": procurement_plan, "entry_stage": ["is", "set"]}, "entry_stage")
	out, seen = [], set()
	for po, supplier in frappe.get_all(
			"Purchase Order Item",
			filters={"material_request": ["in", list(mrs)], "docstatus": ["<", 2]},
			fields=["parent", "parent as p"], as_list=True):
		if po in seen:
			continue
		seen.add(po)
		out.append(frappe._dict({
			"purchase_order": po,
			"supplier": frappe.db.get_value("Purchase Order", po, "supplier"),
			"entry_stage": stage}))
	return out


def _week_of(d):
	if not d:
		return None
	d = getdate(d)
	iso = d.isocalendar()
	return "%s-W%02d" % (iso[0], iso[1])


@frappe.whitelist()
def schedule_for(procurement_plan):
	"""Every drafted order for this plan, as a table somebody can fill in."""
	if not procurement_plan:
		return {"orders": []}
	frappe.has_permission("Summer Flower Procurement Plan", "read",
	                      procurement_plan, throw=True)
	plan = frappe.get_doc("Summer Flower Procurement Plan", procurement_plan)

	# What the plan asked for, per request line, so a confirmed figure can be
	# shown against the thing it is answering rather than against itself.
	asked = {}
	for r in plan.requirements:
		if r.source_doctype == "Material Request" and r.source_doc:
			asked.setdefault(r.source_doc, []).append(r)

	out = []
	for o in _plan_orders(plan.name):
		if not frappe.db.exists("Purchase Order", o.purchase_order):
			continue
		po = frappe.get_doc("Purchase Order", o.purchase_order)
		lines = []
		for i in po.items:
			want = frappe.db.get_value(
				"Material Request Item", i.material_request_item,
				["qty", "schedule_date"], as_dict=True) if i.material_request_item else None
			lines.append({
				"name": i.name,
				"item_code": i.item_code,
				"asked_qty": cint(want.qty) if want else cint(i.qty),
				"asked_week": _week_of(want.schedule_date) if want else _week_of(i.schedule_date),
				"qty": cint(i.qty),
				"schedule_date": str(i.schedule_date) if i.schedule_date else None,
				"week": _week_of(i.schedule_date),
				"received_qty": cint(i.get("received_qty")),
			})
		asked_total = sum(x["asked_qty"] for x in lines)
		qty_total = sum(x["qty"] for x in lines)
		out.append({
			"purchase_order": po.name,
			"supplier": po.supplier,
			"supplier_name": po.get("supplier_name") or po.supplier,
			"status": po.status,
			"docstatus": po.docstatus,
			"editable": 1 if po.docstatus == 0 else 0,
			# What is being bought, in the protocol's own words -- seeds, roots,
			# tubers, plantlets. The card has never been about tissue culture.
			"unit": o.entry_stage or plan.entry_stage or _("units"),
			"confirmation_no": po.get("order_confirmation_no"),
			"confirmation_date": str(po.get("order_confirmation_date") or "") or None,
			"lines": lines,
			"asked_total": asked_total,
			"qty_total": qty_total,
			"short_by": max(0, asked_total - qty_total),
		})
	return {"plan": plan.name, "variety": plan.variety, "farm": plan.farm,
	        "orders": out}


@frappe.whitelist()
def save_schedule(purchase_order, lines):
	"""Write what the supplier says they can send onto the draft order.

	Only a draft is written to. Once the order is submitted it is an agreement,
	and an agreement that one side can quietly rewrite is not one -- ERPNext has
	its own ways to change a submitted order and they leave a trail this does
	not need to duplicate.
	"""
	if isinstance(lines, str):
		lines = frappe.parse_json(lines)
	po = frappe.get_doc("Purchase Order", purchase_order)
	frappe.has_permission("Purchase Order", "write", po, throw=True)
	if po.docstatus != 0:
		frappe.throw(_("{0} has been confirmed already. Its schedule is what both "
		               "sides agreed to, so it is not changed here.").format(po.name),
		             title=_("Already confirmed"))

	by_name = {x.get("name"): x for x in (lines or [])}
	today = getdate(nowdate())
	moved_back = []
	for row in po.items:
		given = by_name.get(row.name)
		if not given:
			continue
		if given.get("qty") not in (None, ""):
			row.qty = max(0, cint(given.get("qty")))
		when = given.get("schedule_date")
		if when:
			when = getdate(when)
			# A delivery cannot be required before the order is placed, and
			# ERPNext refuses the whole document for it -- per row, so one week
			# in the past loses the other thirteen.
			if when < today:
				moved_back.append(str(when))
				when = today
			row.schedule_date = when
	# A line the supplier cannot send at all is not a line. Dropping it here
	# keeps the order a list of deliveries that will happen; the plan still
	# carries what it asked for, which is where the gap is read.
	dropped = [r for r in po.items if cint(r.qty) <= 0]
	if dropped and len(dropped) == len(po.items):
		frappe.throw(_("Every delivery has been set to nothing. Cancel the order "
		               "instead of emptying it."), title=_("Nothing left to send"))
	for r in dropped:
		po.remove(r)
	for n, r in enumerate(po.items, 1):
		r.idx = n
	po.flags.ignore_permissions = True
	po.save()
	if moved_back:
		frappe.msgprint(_(
			"{0} delivery week(s) were before today and have been pulled to today. "
			"An order cannot be wanted before it is placed."
		).format(len(moved_back)), indicator="orange")
	return schedule_for(_plan_of(po.name))


@frappe.whitelist()
def confirm(purchase_order, confirmation_no=None):
	"""Submit the order: the supplier has agreed to this schedule."""
	po = frappe.get_doc("Purchase Order", purchase_order)
	frappe.has_permission("Purchase Order", "submit", po, throw=True)
	if po.docstatus != 0:
		frappe.throw(_("{0} is already confirmed.").format(po.name))
	if not po.items:
		frappe.throw(_("There is nothing to confirm on {0}.").format(po.name))
	po.order_confirmation_no = (confirmation_no
	                            or po.get("order_confirmation_no") or po.name)
	po.order_confirmation_date = nowdate()
	po.flags.ignore_permissions = True
	po.submit()
	plan = _plan_of(po.name)
	if plan:
		_write_back(plan)
	return schedule_for(plan)


def _plan_of(purchase_order):
	"""The procurement plan an order answers, through the request it came from."""
	mr = frappe.db.get_value("Purchase Order Item",
	                         {"parent": purchase_order, "material_request": ["is", "set"]},
	                         "material_request")
	if not mr:
		return None
	return frappe.db.get_value(
		"Planting Material Requirement",
		{"source_doc": mr, "parenttype": "Summer Flower Procurement Plan"}, "parent")


def _write_back(procurement_plan):
	"""Put the confirmed week and number back on the requirement lines.

	The plan asked for one thing and is getting another, and the difference is
	the only part of this worth reading later: a planting whose material comes
	a fortnight late, or short by a third, is a planting that will not produce
	what the plan says it will. Stored on the line rather than worked out on
	demand because a confirmation is a fact about a moment -- what was agreed,
	when -- and an order can be received, closed or cancelled afterwards without
	that ever having been untrue.
	"""
	plan = frappe.get_doc("Summer Flower Procurement Plan", procurement_plan)
	confirmed = {}
	for o in _plan_orders(plan.name):
		if not frappe.db.exists("Purchase Order", o.purchase_order):
			continue
		po = frappe.get_doc("Purchase Order", o.purchase_order)
		if po.docstatus != 1:
			continue
		for i in po.items:
			if i.material_request_item:
				confirmed[i.material_request_item] = i

	if not confirmed:
		return
	# A requirement line reaches its order line through the request line it
	# became, which raise_material_requests records as it writes them. One
	# requirement line is one delivery is one request line is one order line,
	# which is why the grouping puts a line per delivery on the request.
	for r in plan.requirements:
		row = confirmed.get(r.get("source_row"))
		if not row:
			continue
		r.db_set("confirmed_delivery_week", _week_of(row.schedule_date),
		         update_modified=False)
		r.db_set("confirmed_qty", cint(row.qty), update_modified=False)
		# Set both ways. A flag that is only ever raised is a flag that stays
		# raised: a line short on the first confirmation and covered on the
		# second still read as short, and the plan counted it.
		r.db_set("short_ordered", 1 if cint(row.qty) < cint(r.qty_to_order) else 0,
		         update_modified=False)
