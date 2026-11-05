# Copyright (c) 2026, James Kiruga and contributors
"""What the supplier has agreed to send, week by week."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers.planning import season_year


def _plan_orders(procurement_plan):
	"""The orders drafted for this plan."""
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
	asked = {}
	for r in plan.requirements:
		if r.source_doctype == "Material Request" and r.source_doc:
			asked.setdefault(r.source_doc, []).append(r)

	# Before anything is raised, the thing to confirm against is the plan's own
	if not _plan_orders(plan.name):
		return {"plan": plan.name, "variety": plan.variety, "farm": plan.farm,
		        "production_plan": plan.production_plan,
		        "impact": commitment_impact(plan),
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
			"needs_sellers": 0,
			"sellers": [po.supplier] if po.supplier else [],
			"supplier": po.supplier,
			"supplier_name": po.get("supplier_name") or po.supplier,
			"status": po.status,
			"docstatus": po.docstatus,
			"editable": 1 if po.docstatus == 0 else 0,
			# What is being bought, in the protocol's own words -- seeds, roots,
			"unit": o.entry_stage or plan.entry_stage or _("units"),
			"confirmation_no": po.get("order_confirmation_no"),
			"confirmation_date": str(po.get("order_confirmation_date") or "") or None,
			"lines": lines,
			"asked_total": asked_total,
			"qty_total": qty_total,
			"short_by": max(0, asked_total - qty_total),
		})
	return {"plan": plan.name, "variety": plan.variety, "farm": plan.farm,
	        "production_plan": plan.production_plan,
	        # What the commitment means for the crop, not for the ledger: the
	        # buyer reads units, the grower reads plants.
	        "impact": commitment_impact(plan),
	        "orders": out}


def _sellers_of(plan, row, stage):
	"""Who a week's units come from: its own seller, else the allocation."""
	if row.supplier:
		return [(row.supplier, 100.0)]
	rows = plan.suppliers_for(stage)
	if not rows:
		return [(row.supplier or plan.supplier, 100.0)]
	return [(r.supplier, flt(r.share_pct)) for r in rows]


def commitment_impact(plan):
	"""What the sellers have committed to, said in plants instead of units."""
	stages, by_seller = {}, {}
	units_asked = units_given = plants_planned = plants_landing = 0
	for r in plan.purchase_lines():
		stage = r.entry_stage or plan.entry_stage or ""
		ordered = cint(r.qty_to_order)
		answered = cint(r.get("delivery_confirmed"))
		given = cint(r.confirmed_qty) if answered else ordered
		at_field = cint(r.qty_at_field)
		# A unit is worth what the line says it is worth, so a short delivery
		# lands the same share of the plants it was bought to stand up.
		lands = int(at_field * given / ordered) if ordered else 0
		units_asked += ordered
		units_given += given
		plants_planned += at_field
		plants_landing += lands
		st = stages.setdefault(stage, {"stage": stage, "units_asked": 0,
		                               "units_given": 0, "plants_planned": 0,
		                               "plants_landing": 0, "weeks": 0,
		                               "weeks_answered": 0})
		st["units_asked"] += ordered
		st["units_given"] += given
		st["plants_planned"] += at_field
		st["plants_landing"] += lands
		st["weeks"] += 1
		st["weeks_answered"] += 1 if answered else 0
		sellers = _sellers_of(plan, r, stage)
		total = sum(sh for _s, sh in sellers) or 100.0
		taken_u = taken_p = 0
		for i, (name, share) in enumerate(sellers):
			last = i == len(sellers) - 1
			u = given - taken_u if last else int(round(given * share / total))
			pl = lands - taken_p if last else int(round(lands * share / total))
			taken_u += u
			taken_p += pl
			if not (u or pl):
				continue
			k = (name or "", stage)
			e = by_seller.setdefault(k, {
				"supplier": name or _("nobody named"), "stage": stage,
				"units": 0, "plants": 0, "weeks": 0, "first_week": None,
				"placed": 1 if r.supplier else 0})
			e["units"] += u
			e["plants"] += pl
			e["weeks"] += 1
			when = r.confirmed_delivery_week or _week_of(r.required_at_site_date)
			if when and (not e["first_week"] or when < e["first_week"]):
				e["first_week"] = when
	rows = sorted(by_seller.values(), key=lambda x: -x["plants"])
	for x in rows:
		x["pct_of_plants"] = (round(100.0 * x["plants"] / plants_planned, 1)
		                      if plants_planned else 0)
	return {
		"rows": rows,
		"stages": sorted(stages.values(), key=lambda x: -x["plants_planned"]),
		"units_asked": units_asked, "units_given": units_given,
		"plants_planned": plants_planned, "plants_landing": plants_landing,
		"short_by_plants": max(0, plants_planned - plants_landing),
		"short_by_units": max(0, units_asked - units_given),
		"answered": any(cint(r.get("delivery_confirmed"))
		                for r in plan.purchase_lines()),
	}


def _from_requirements(plan):
	"""The plan's own wants, shaped like an order so one table draws both."""
	lines, asked, qty = [], 0, 0
	# What is bought, and nothing else. A route that digs its material off the
	for r in plan.purchase_lines():
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
			"supplier": r.supplier,
			"qty": agreed,
			# The date keyed so far, else the one the plan asked for.
			"schedule_date": str(r.expected_delivery_date
			                     or r.required_at_site_date or "") or None,
			"week": r.confirmed_delivery_week or _week_of(r.required_at_site_date),
			"received_qty": 0,
		})
	# Named off the lines being bought. Taking the first stage on the document
	stage = next((r.entry_stage for r in plan.purchase_lines() if r.entry_stage), None)
	sellers = [{"supplier": r.supplier, "share_pct": flt(r.share_pct)}
	           for r in (plan.get("purchase_allocation") or []) if r.supplier]
	return {
		"purchase_order": None, "requirement_of": plan.name,
		# Nothing to confirm against until somebody says who sells it: an order
		"sellers": [x["supplier"] for x in sellers],
		"needs_sellers": 0 if sellers else 1,
		# Not the first seller. A plan split between two of them has no single
		# supplier, and naming one made the screen read as if it had.
		"supplier": plan.supplier or (sellers[0]["supplier"]
		                              if len(sellers) == 1 else None),
		# Named from the sellers, not from the plan's single supplier field --
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
	"""Write what the supplier says they can send onto the plan's own lines."""
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
		# Present and empty is an answer: split this week between the sellers by
		# the shares on the allocation. Absent leaves the row as it is.
		if "supplier" in given:
			r.db_set("supplier", given.get("supplier") or None,
			         update_modified=False)
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
	"""Agree the schedule, then raise the request and the order for it."""
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
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			write_plantings,
		)

	# The plan said what we wanted; the deliveries say what we get. Approve a
	# revision carrying the second before anything is written off it.
	revised = revise_to_confirmed(doc.name)
	if revised.get("revised"):
		doc.reload()
	plan = frappe.get_doc("Summer Flower Production Plan",
	                      revised.get("revised") or doc.production_plan)
	write_plantings(plan, doc)
	# And the bench is told what to raise, now that there is a confirmed figure
	if doc.raises_here():
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
	# Where the production plan went, so the screen that asked for this can
	# follow it instead of looking at a plan that is no longer the live one.
	out = schedule_for(doc.name)
	out["production_plan"] = doc.production_plan
	out["revised_to"] = revised.get("revised")
	return out


# Everything that points at a production plan by name. A revision is a new
# document, so what hung off the old one has to be told where it went.
POINTS_AT_PLAN = [
	("Summer Flower Procurement Plan", "production_plan"),
	("Summer Flower Propagation Plan", "production_plan"),
	("Summer Flower Motherstock Plan", "production_plan"),
	("Summer Flower Motherstock Batch", "production_plan"),
	("Summer Flower Season Plan", "production_plan"),
	("Summer Flower Budget", "production_plan"),
]


def _confirmed_by_cohort(proc, plan):
	"""What each planting can actually stand up, off the confirmed deliveries."""
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			confirmed_plants,
		)

	# In order, not by row name: a revision is a copy, and a copy's rows carry
	# new names. Position is what survives copying.
	out = []
	for row in plan.plan_blocks:
		if not cint(row.is_new_planting):
			continue
		lines = [x for x in (proc.requirements or []) if x.cohort == row.name]
		wanted = cint(row.get("plants_to_plant")) or cint(row.plants)
		landing, short = confirmed_plants(lines, wanted)
		out.append({"row": row.name, "landing": landing, "short": short,
		            "answered": 1 if lines else 0})
	return out


@frappe.whitelist()
def revise_to_confirmed(procurement_plan, force=0):
	"""Re-cut the production plan to the quantities the sellers committed to.

	An approved plan is a commitment, so it is not edited: it is cancelled and
	a revision of it is approved in its place, carrying what will really be
	planted."""
	proc = frappe.get_doc("Summer Flower Procurement Plan", procurement_plan)
	frappe.has_permission("Summer Flower Procurement Plan", "write", proc, throw=True)
	if not proc.production_plan:
		return {"revised": None, "why": _("This plan sources nothing.")}
	plan = frappe.get_doc("Summer Flower Production Plan", proc.production_plan)
	if plan.docstatus == 2:
		return {"revised": None, "why": _("{0} is cancelled.").format(plan.name)}
	if not any(cint(r.get("delivery_confirmed")) for r in (proc.requirements or [])):
		return {"revised": None,
		        "why": _("Nothing has been confirmed, so there is nothing to revise to.")}

	targets = _confirmed_by_cohort(proc, plan)
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			plants_per_bed_for, plants_standing_from,
		)

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	per_bed = cint(plants_per_bed_for(v))
	rows = [b for b in plan.plan_blocks if cint(b.is_new_planting)]
	moved, lost = [], 0
	for row, want in zip(rows, targets):
		if not cint(want["answered"]):
			continue
		standing = (cint(plants_standing_from(v, want["landing"]))
		            if want["landing"] else 0)
		if standing != cint(row.plants):
			moved.append((row.name, cint(row.plants), standing))
			lost += max(0, cint(row.plants) - standing)
	if not moved and not cint(force):
		return {"revised": None, "plan": plan.name,
		        "why": _("{0} already says what the sellers confirmed.").format(plan.name)}

	# A draft needs no revision: it is simply written.
	if plan.docstatus == 0:
		_write_confirmed(plan, targets, v, per_bed)
		plan.flags.plantings_are_confirmed = True
		plan.flags.ignore_permissions = True
		plan.save()
		return {"revised": plan.name, "amended": 0, "rows": len(moved),
		        "plants_lost": lost}

	# Approved. Cancel it and approve a revision in its place, so the number
	# somebody committed to and the number that will be planted are two
	# documents rather than one that quietly changed.
	plan.flags.ignore_links = True
	plan.flags.ignore_permissions = True
	plan.cancel()

	new = frappe.copy_doc(plan)
	new.amended_from = plan.name
	new.docstatus = 0
	new.status = "Draft"
	# Cleared, not copied. A new document that opens already in the approved
	# state is a transition out of nowhere, and the workflow refuses it: the
	# revision walks the same road the plan walked.
	_clear_workflow_state(new)
	_write_confirmed(new, targets, v, per_bed)
	new.flags.plantings_are_confirmed = True
	new.flags.skip_raise = True
	new.flags.ignore_permissions = True
	new.insert()
	approved = _walk_to_approved(new)

	# The requirement lines name the plantings they buy for, by row. A copy's
	# rows are new rows, so without this every line points at a planting on a
	# cancelled plan and nothing downstream can match them up again.
	was = [t["row"] for t in targets]
	now = [b.name for b in new.plan_blocks if cint(b.is_new_planting)]
	moved_rows = dict(zip(was, now))
	for r in (proc.requirements or []):
		if r.cohort in moved_rows:
			frappe.db.set_value("Planting Material Requirement", r.name, "cohort",
			                    moved_rows[r.cohort], update_modified=False)

	for doctype, field in POINTS_AT_PLAN:
		for name in frappe.get_all(doctype, filters={field: plan.name}, pluck="name"):
			frappe.db.set_value(doctype, name, field, new.name,
			                    update_modified=False)
	# The calendar is what the farm plants. A planting already written from the
	# old plan would otherwise stand there at the figure the delivery just
	# contradicted, and the writer skips rows that already have an entry.
	revised_rows = _revise_calendar(new, proc.name)
	frappe.db.commit()
	frappe.msgprint(
		_("{0} {1} in place of {2}: {3} planting(s) re-cut to what the sellers "
		  "confirmed{4}."
		  ).format(frappe.utils.get_link_to_form(
			"Summer Flower Production Plan", new.name),
			_("is approved") if approved else _("is waiting for approval"),
			plan.name, len(moved),
			_(", {0} plants fewer").format("{:,}".format(lost)) if lost else ""),
		title=_("The plan has been revised"), indicator="orange")
	return {"revised": new.name, "amended": 1, "was": plan.name,
	        "approved": approved, "state": new.get("workflow_state"),
	        "rows": len(moved), "plants_lost": lost,
	        "plantings_revised": revised_rows}


def _revise_calendar(plan, procurement_plan):
	"""Bring plantings already on the calendar down to the confirmed figures."""
	done = 0
	for row in plan.plan_blocks:
		if not cint(row.is_new_planting) or not row.existing_planting:
			continue
		if not frappe.db.exists("Planting Calendar", row.existing_planting):
			continue
		doc = frappe.get_doc("Planting Calendar", row.existing_planting)
		# What is in the ground, or out of it, is not a plan any more.
		if doc.get("calendar_status") in ("Planted", "Uprooted", "Cancelled"):
			continue
		if cint(doc.plants) == cint(row.plants) and cint(doc.beds) == cint(row.beds):
			continue
		if not cint(doc.get("plants_before_revision")):
			doc.beds_before_revision = doc.beds
			doc.plants_before_revision = doc.plants
		doc.plants = cint(row.plants)
		doc.beds = cint(row.beds)
		if not cint(row.plants):
			doc.calendar_status = "Cancelled"
		doc.revision_reason = _(
			"Revised to what the sellers confirmed on {0}.").format(procurement_plan)
		doc.revised_on = frappe.utils.now_datetime()
		doc.revised_by = frappe.session.user
		doc.flags.ignore_permissions = True
		doc.flags.ignore_validate_update_after_submit = True
		doc.save()
		done += 1
	return done


def _clear_workflow_state(doc):
	"""Put a copied document back at the start of its workflow."""
	from frappe.model.workflow import get_workflow_name

	wf = get_workflow_name(doc.doctype)
	if not wf:
		return
	field = frappe.db.get_value("Workflow", wf, "workflow_state_field")
	if field and doc.meta.has_field(field):
		doc.set(field, None)


def _walk_to_approved(doc):
	"""Take a revision as far through its workflow as this user may take it."""
	from frappe.model.workflow import (apply_workflow, get_transitions,
	                                   get_workflow_name)

	if not get_workflow_name(doc.doctype):
		doc.submit()
		return 1
	# Four is the longest road any of these workflows has; a loop without a
	# bound is one bad transition away from spinning.
	for _step in range(4):
		if cint(doc.docstatus) == 1:
			return 1
		try:
			moves = [t.action for t in get_transitions(doc)]
		except Exception:
			break
		# Forward only. Rejecting a revision nobody has read is not progress.
		nxt = next((a for a in moves
		            if a not in ("Reject", "Reopen", "Cancel")), None)
		if not nxt:
			break
		try:
			doc = apply_workflow(doc, nxt)
		except Exception:
			break
	return 1 if cint(doc.docstatus) == 1 else 0


def _write_confirmed(plan, targets, v, per_bed):
	"""Put the confirmed figures on the plantings of a plan being written."""
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			plants_standing_from, size_planting,
		)

	rows = [b for b in plan.plan_blocks if cint(b.is_new_planting)]
	for row, want in zip(rows, targets):
		if not cint(want["answered"]):
			continue
		landing = cint(want["landing"])
		if not cint(row.get("plants_from_demand")):
			row.plants_from_demand = cint(row.plants)
		standing = cint(plants_standing_from(v, landing)) if landing else 0
		row.plants = standing
		row.plants_to_plant = landing
		# Beds off what stands, the way every other planting on the plan is
		# sized, so a short delivery gives the ground back.
		row.beds = cint(size_planting(v, standing)[0]) if standing else 0
		if row.meta.has_field("trimmed_to_materials"):
			row.trimmed_to_materials = 1 if cint(want["short"]) else 0


@frappe.whitelist()
def save_schedule(purchase_order, lines):
	"""Write what the supplier says they can send onto the draft order."""
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
			if when < today:
				moved_back.append(str(when))
				when = today
			row.schedule_date = when
	# A line the supplier cannot send at all is not a line. Dropping it here
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
	"""Put the confirmed week and number back on the requirement lines."""
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
	for r in plan.requirements:
		if r.source_doc not in answered:
			continue
		row = confirmed.get(r.get("source_row"))
		if not row:
			# Struck out. Nothing is coming for this week, and saying so is the
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
		r.db_set("short_ordered", 1 if cint(row.qty) < cint(r.qty_to_order) else 0,
		         update_modified=False)
