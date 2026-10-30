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

	# Before anything is raised, the thing to confirm against is the plan's own
	# requirement lines: what the farm wants, week by week. There is no order
	# yet and there should not be -- what is ordered is what a supplier agreed
	# to, and nobody has been asked. Once the schedule IS agreed the order
	# carries it, and this reads that instead.
	if not _plan_orders(plan.name):
		return {"plan": plan.name, "variety": plan.variety, "farm": plan.farm,
		        "orders": [_from_requirements(plan)]}

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
				"supplier": po.supplier,
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
			"requirement_of": None,
			# The same two keys the requirement-sourced shape carries, so one
			# renderer draws both without asking which it is looking at.
			"needs_sellers": 0,
			"sellers": [po.supplier] if po.supplier else [],
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


def _from_requirements(plan):
	"""The plan's own wants, shaped like an order so one table draws both."""
	lines, asked, qty = [], 0, 0
	for r in plan.requirements:
		if not cint(r.qty_to_order):
			continue
		want = cint(r.qty_to_order)
		# What has been keyed so far, or the ask where nothing has.
		agreed = cint(r.confirmed_qty) if cint(r.get("delivery_confirmed")) else want
		asked += want
		qty += agreed
		lines.append({
			"name": r.name, "item_code": plan.variety,
			"asked_qty": want,
			"asked_week": _week_of(r.required_at_site_date),
			# Which seller this week's delivery comes from. A crop bought from
			# two labs is not two proportions of every week -- it is some weeks
			# from one and some from the other -- and that is a thing somebody
			# decides, week by week, not an arithmetic split.
			"supplier": r.supplier,
			"qty": agreed,
			# The date keyed so far, else the one the plan asked for.
			"schedule_date": str(r.expected_delivery_date
			                     or r.required_at_site_date or "") or None,
			"week": r.confirmed_delivery_week or _week_of(r.required_at_site_date),
			"received_qty": 0,
		})
	stage = next((r.entry_stage for r in plan.requirements if r.entry_stage), None)
	sellers = [{"supplier": r.supplier, "share_pct": flt(r.share_pct)}
	           for r in (plan.get("purchase_allocation") or []) if r.supplier]
	return {
		"purchase_order": None, "requirement_of": plan.name,
		# Nothing to confirm against until somebody says who sells it: an order
		# has to be placed with a supplier, and a schedule agreed with nobody is
		# not an agreement.
		"sellers": [x["supplier"] for x in sellers],
		"needs_sellers": 0 if sellers else 1,
		"supplier": plan.supplier or (sellers[0]["supplier"] if sellers else None),
		# Named from the sellers, not from the plan's single supplier field --
		# which is blank whenever a crop is bought from more than one, and so
		# read "not chosen yet" over a table listing two of them.
		"supplier_name": (" + ".join(x["supplier"] for x in sellers)
		                  if sellers else (plan.supplier or _("nobody yet"))),
		"status": _("Not ordered"), "docstatus": 0, "editable": 1,
		"unit": stage or plan.entry_stage or _("units"),
		"confirmation_no": None, "confirmation_date": None,
		"lines": lines, "asked_total": asked, "qty_total": qty,
		"short_by": max(0, asked - qty),
	}


@frappe.whitelist()
def save_requirements(procurement_plan, lines):
	"""Write what the supplier says they can send onto the plan's own lines.

	Before an order exists this is where a confirmation lives. Nothing is
	ordered by saving it: the quantities are the farm's record of what was
	agreed, and raising the documents is a separate, deliberate step.
	"""
	if isinstance(lines, str):
		lines = frappe.parse_json(lines)
	doc = frappe.get_doc("Summer Flower Procurement Plan", procurement_plan)
	frappe.has_permission("Summer Flower Procurement Plan", "write", doc, throw=True)
	by_name = {x.get("name"): x for x in (lines or [])}
	today = getdate(nowdate())
	for r in doc.requirements:
		given = by_name.get(r.name)
		if not given:
			continue
		if given.get("qty") not in (None, ""):
			r.db_set("confirmed_qty", max(0, cint(given.get("qty"))),
			         update_modified=False)
		if given.get("supplier"):
			r.db_set("supplier", given.get("supplier"), update_modified=False)
		when = given.get("schedule_date")
		if when:
			when = max(getdate(when), today)
			r.db_set("confirmed_delivery_week", _week_of(when),
			         update_modified=False)
			r.db_set("expected_delivery_date", when, update_modified=False)
		r.db_set("delivery_confirmed", 1, update_modified=False)
		r.db_set("short_ordered",
		         1 if cint(r.confirmed_qty) < cint(r.qty_to_order) else 0,
		         update_modified=False)
	return schedule_for(doc.name)


@frappe.whitelist()
def confirm_and_raise(procurement_plan):
	"""Agree the schedule, then raise the request and the order for it.

	The order of these two is the whole point. The documents carry what a
	supplier said they would send, so nothing has to be amended the first time
	a lab says no -- and the plan is told, because a planting fed by a short
	delivery is a smaller planting.
	"""
	doc = frappe.get_doc("Summer Flower Procurement Plan", procurement_plan)
	frappe.has_permission("Summer Flower Procurement Plan", "write", doc, throw=True)
	if not any(cint(r.get("delivery_confirmed")) for r in doc.requirements):
		frappe.throw(_("Nothing has been confirmed yet. Key in what the supplier "
		               "can send, week by week, and save it first."),
		             title=_("Nothing to raise"))
	doc.db_set("deliveries_confirmed", 1, update_modified=False)
	doc.reload()
	doc.raise_material_requests()
	# A short confirmation makes a smaller planting, and the plan has to be
	# rebuilt from it or the calendar goes on believing what was asked for.
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			write_plantings,
		)

	plan = frappe.get_doc("Summer Flower Production Plan", doc.production_plan)
	write_plantings(plan, doc)
	# And the bench is told what to raise, now that there is a confirmed figure
	# to raise. Where an outside propagator has the share instead, the order
	# raised above is their instruction and the plan carries the week the plants
	# are due -- plants_from on the allocation, which the card shows.
	if cint(doc.get("propagates_here")) or doc.get("propagation_allocation"):
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_propagation_plan.summer_flower_propagation_plan import (
				build_from_plan,
			)

		try:
			build_from_plan(doc.production_plan)
		except Exception as e:
			frappe.msgprint(_("The orders are raised; the propagation plan could "
			                  "not be built: {0}").format(str(e).split("\n")[0][:160]),
			                indicator="orange", title=_("Propagation plan"))
	frappe.db.commit()
	return schedule_for(doc.name)


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

	# Every request that now sits under a confirmed order. A line of one of
	# these that has NO order line left has been answered too: the supplier
	# struck that delivery out, and save_schedule dropped it. Without this it
	# reads as a question nobody answered, and the planting goes in at full
	# size against a delivery that is never coming.
	answered = set()
	for o in _plan_orders(plan.name):
		if not frappe.db.exists("Purchase Order", o.purchase_order):
			continue
		po = frappe.get_doc("Purchase Order", o.purchase_order)
		if po.docstatus != 1:
			continue
		answered.update(i.material_request for i in po.items if i.material_request)
	if not answered:
		return
	# A requirement line reaches its order line through the request line it
	# became, which raise_material_requests records as it writes them. One
	# requirement line is one delivery is one request line is one order line,
	# which is why the grouping puts a line per delivery on the request.
	for r in plan.requirements:
		if r.source_doc not in answered:
			continue
		row = confirmed.get(r.get("source_row"))
		if not row:
			# Struck out. Nothing is coming for this week, and saying so is the
			# whole point: a planting with no material is not a smaller planting.
			r.db_set("delivery_confirmed", 1, update_modified=False)
			r.db_set("confirmed_qty", 0, update_modified=False)
			r.db_set("confirmed_delivery_week", None, update_modified=False)
			r.db_set("short_ordered", 1, update_modified=False)
			continue
		r.db_set("delivery_confirmed", 1, update_modified=False)
		r.db_set("confirmed_delivery_week", _week_of(row.schedule_date),
		         update_modified=False)
		r.db_set("confirmed_qty", cint(row.qty), update_modified=False)
		# Set both ways. A flag that is only ever raised is a flag that stays
		# raised: a line short on the first confirmation and covered on the
		# second still read as short, and the plan counted it.
		r.db_set("short_ordered", 1 if cint(row.qty) < cint(r.qty_to_order) else 0,
		         update_modified=False)
