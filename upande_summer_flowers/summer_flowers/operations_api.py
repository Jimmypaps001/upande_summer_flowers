# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Transactional endpoints for the planning dashboard.

planning_api is a read-only projection layer. This module is the opposite: every
call here writes. It is kept separate so that "does this endpoint change data"
never has to be answered by reading the body of the function.

Nothing here reimplements business rules. Each endpoint loads the document and
calls the controller method, so the dashboard is subject to exactly the same
validation, approval and role checks as the desk form -- an approval cannot be
skipped by coming in through the web page.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

REVIEW_SOURCES = (
	# (doctype, status field, pending value, label, extra fields for the card)
	("Summer Flower Production Plan", "workflow_state", "Pending Approval",
	 "Production Plan",
	 ["market_demand", "variety", "farm", "total_demand_stems",
	  "total_production_stems", "coverage_pct", "weeks_in_deficit"]),
	("Crop Protocol Version", "version_status", "Pending Approval",
	 "Protocol Version", ["variety", "farm", "version", "change_reason"]),
	("Block Production Budget", "status", "Pending Approval", "Block Budget",
	 ["block", "variety", "from_date", "to_date", "total_budgeted", "currency"]),
	("Block Change Request", "status", "Pending Approval", "Block Change",
	 ["request_type", "farm", "effective_date", "source_area_ha", "result_area_ha",
	  "justification"]),
)

# Only these controller methods may be reached from the dashboard, and only on
# these doctypes. A generic "call any method" endpoint would be a hole.
ALLOWED_ACTIONS = {
	"Block Production Budget": ("submit_for_approval", "approve", "reject",
	                            "refresh_actuals"),
	"Block Change Request": ("submit_for_approval", "approve", "reject", "execute"),
	"Crop Cycle": ("approve_protocol_override", "approve_uproot_deviation",
	               "submit_targets_for_validation", "validate_targets",
	               "reject_targets", "record_cuttings", "end_cycle"),
	"Summer Flower Budget": ("post_to_accounts",),
	"Summer Flower Production Plan": ("create_plantings", "regenerate"),
	"Summer Flower Propagation Plan": ("submit_for_approval", "approve", "reject",
	                                   "create_motherstock_batch",
	                                   "create_seedling_requests"),
}


def _guard():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)


def _cycle(name):
	_guard()
	doc = frappe.get_doc("Crop Cycle", name)
	if not doc.get("custom_is_summer_flower_cycle"):
		frappe.throw(_("{0} is not a summer flower cycle.").format(name))
	return doc


# ---------------------------------------------------------------------------
# Crop cycles: list and detail
# ---------------------------------------------------------------------------

@frappe.whitelist()
def crop_cycles(variety=None, farm=None, block=None, status=None):
	"""Every summer flower cycle in scope, with what it owes and what it has cut."""
	_guard()
	f = {"custom_is_summer_flower_cycle": 1}
	if variety:
		f["custom_sf_variety"] = variety
	if farm:
		f["farm"] = farm
	if block:
		f["custom_block"] = block
	if status:
		f["custom_sf_cycle_status"] = status
	rows = frappe.get_all(
		"Crop Cycle", filters=f,
		fields=["name", "custom_block", "custom_sf_variety", "farm",
		        "custom_sf_cycle_status", "custom_planting_date",
		        "custom_planned_pinch_date", "custom_actual_pinch_date",
		        "custom_planned_uproot_date", "custom_actual_uproot_date",
		        "custom_plant_age_weeks", "custom_live_plant_count",
		        "custom_current_flush_number", "custom_next_harvest_date",
		        "custom_expected_stems_next_flush", "custom_flushes_remaining",
		        "custom_expected_stems_life", "custom_total_harvested_stems",
		        "custom_harvest_variance_stems", "custom_expected_stems_remaining",
		        "custom_targets_status", "custom_has_protocol_override",
		        "custom_override_approved_by", "custom_uproot_deviation_days",
		        "custom_uproot_approved_by", "custom_royalty_applicable",
		        "custom_total_royalty_payable", "custom_royalty_currency",
		        "custom_cuttings_taken", "custom_area_planted_sqm",
		        "custom_area_utilisation_pct", "custom_crop_protocol_version"],
		order_by="custom_planting_date asc",
	)
	for r in rows:
		r["block_code"] = str(r.custom_block or "").split(" - Block ").pop()
		# What still needs a person: the dashboard sorts its work off these.
		r["needs"] = []
		if r.custom_has_protocol_override and not r.custom_override_approved_by:
			r["needs"].append("override approval")
		if cint(r.custom_uproot_deviation_days) and not r.custom_uproot_approved_by:
			r["needs"].append("uproot approval")
		if r.custom_targets_status == "Pending Validation":
			r["needs"].append("target validation")
	due = [r for r in rows if r.custom_next_harvest_date]
	return {
		"cycles": rows,
		"totals": {
			"cycles": len(rows),
			"active": len([r for r in rows if r.custom_sf_cycle_status == "Active"]),
			"ended": len([r for r in rows if r.custom_sf_cycle_status == "Ended"]),
			"partially_uprooted": len(
				[r for r in rows if r.custom_sf_cycle_status == "Partially Uprooted"]),
			"plants_standing": sum(cint(r.custom_live_plant_count) for r in rows),
			"harvested": sum(cint(r.custom_total_harvested_stems) for r in rows),
			"expected_life": sum(cint(r.custom_expected_stems_life) for r in rows),
			"remaining": sum(cint(r.custom_expected_stems_remaining) for r in rows),
			"variance": sum(cint(r.custom_harvest_variance_stems) for r in rows),
			"needing_attention": len([r for r in rows if r["needs"]]),
			"next_harvest": min((str(r.custom_next_harvest_date) for r in due),
			                    default=None),
		},
	}


@frappe.whitelist()
def crop_cycle_detail(cycle):
	"""One cycle in full: flushes, per-grade targets and the override state."""
	doc = _cycle(cycle)
	v = frappe.get_cached_doc("Crop Protocol Version",
	                          doc.custom_crop_protocol_version)
	today = getdate(nowdate())
	flushes = []
	for r in doc.custom_flush_schedule:
		flushes.append({
			"name": r.name, "flush_number": r.flush_number,
			"harvest_date": str(r.harvest_date), "year": r.year,
			"week_no": r.week_no, "label": "%s-W%02d" % (r.year, r.week_no),
			"stems_per_plant": flt(r.stems_per_plant),
			"expected_stems": cint(r.expected_stems),
			"plants_standing": cint(r.plants_standing),
			"is_harvested": cint(r.is_harvested),
			"actual_stems": cint(r.actual_stems),
			"variance_stems": cint(r.variance_stems),
			# A flush cannot be cut before it is due.
			"is_due": bool(r.harvest_date and getdate(r.harvest_date) <= today),
		})
	targets = [{
		"name": r.name, "year": r.year, "week_no": r.week_no,
		"label": "%s-W%02d" % (r.year, r.week_no),
		"week_start_date": str(r.week_start_date or ""),
		"flush_number": r.flush_number, "grade": r.grade,
		"expected_stems": cint(r.expected_stems),
		"target_stems": cint(r.target_stems),
		"is_manual": cint(r.is_manual),
		"actual_stems": cint(r.actual_stems),
		"variance_stems": cint(r.variance_stems),
	} for r in doc.custom_weekly_targets]

	return {
		"cycle": doc.name,
		"block": doc.custom_block,
		"block_code": str(doc.custom_block or "").split(" - Block ").pop(),
		"variety": doc.custom_sf_variety,
		"farm": doc.farm,
		"planting_calendar": doc.custom_planting_calendar,
		"protocol": doc.custom_crop_protocol_version,
		"protocol_is_current": cint(v.is_current),
		"market_demand": doc.custom_market_demand,
		"status": doc.custom_sf_cycle_status,
		"dates": {
			"planting": str(doc.custom_planting_date or ""),
			"planned_pinch": str(doc.custom_planned_pinch_date or ""),
			"actual_pinch": str(doc.custom_actual_pinch_date or ""),
			"planned_uproot": str(doc.custom_planned_uproot_date or ""),
			"actual_uproot": str(doc.custom_actual_uproot_date or ""),
			"plant_age_weeks": cint(doc.custom_plant_age_weeks),
			"uproot_deviation_days": cint(doc.custom_uproot_deviation_days),
			"uproot_reason": doc.custom_uproot_deviation_reason,
			"uproot_approved_by": doc.custom_uproot_approved_by,
		},
		"geometry": {
			"area_planted_sqm": flt(doc.custom_area_planted_sqm),
			"block_gross_sqm": flt(doc.custom_block_gross_area_sqm),
			"utilisation_pct": flt(doc.custom_area_utilisation_pct),
			"density": flt(doc.custom_planting_density_per_sqm),
			"beds": cint(doc.custom_beds_planted),
			"live_plants": cint(doc.custom_live_plant_count),
			"cuttings_taken": cint(doc.custom_cuttings_taken),
		},
		"harvest": {
			"current_flush": cint(doc.custom_current_flush_number),
			"next_date": str(doc.custom_next_harvest_date or ""),
			"expected_next": cint(doc.custom_expected_stems_next_flush),
			"flushes_remaining": cint(doc.custom_flushes_remaining),
			"expected_life": cint(doc.custom_expected_stems_life),
			"harvested": cint(doc.custom_total_harvested_stems),
			"variance": cint(doc.custom_harvest_variance_stems),
			"remaining": cint(doc.custom_expected_stems_remaining),
			"last_date": str(doc.custom_last_harvest_date or ""),
		},
		"override": {
			"active": cint(doc.custom_has_protocol_override),
			"justification": doc.custom_override_justification,
			"approved_by": doc.custom_override_approved_by,
			"approved_on": str(doc.custom_override_approved_on or ""),
			"originals": doc.custom_protocol_original_values,
			"weeks_to_pinch": cint(doc.custom_ovr_weeks_to_pinch),
			"flush_interval_weeks": cint(doc.custom_ovr_flush_interval_weeks),
			"first_harvest_offset_weeks": cint(
				doc.custom_ovr_first_harvest_offset_weeks),
			"total_weeks_in_ground": cint(doc.custom_ovr_total_weeks_in_ground),
			"protocol": {
				"weeks_to_pinch": cint(v.weeks_to_pinch),
				"flush_interval_weeks": cint(v.flush_interval_weeks),
				"first_harvest_offset_weeks": cint(v.first_harvest_offset_weeks),
				"total_weeks_in_ground": cint(v.total_weeks_in_ground),
			},
		},
		"royalty": {
			"applicable": cint(doc.custom_royalty_applicable),
			"rate": flt(doc.custom_royalty_rate),
			"currency": doc.custom_royalty_currency,
			"payable": flt(doc.custom_total_royalty_payable),
			"invoice": doc.custom_royalty_invoice,
		},
		"targets_status": doc.custom_targets_status,
		"targets_validated_by": doc.custom_targets_validated_by,
		"target_total": cint(doc.custom_target_total_stems),
		"flushes": flushes,
		"targets": targets,
	}


# ---------------------------------------------------------------------------
# Harvesting
# ---------------------------------------------------------------------------

@frappe.whitelist()
def record_harvest(cycle, flush_number, actual_stems, harvest_date=None,
                   grade_actuals=None):
	"""Close a flush with what was actually cut.

	Per-grade actuals are optional. When given they are written onto the weekly
	target rows for the flush's week and must add up to the flush total, because
	a grade split that does not reconcile makes both figures untrustworthy.
	"""
	doc = _cycle(cycle)
	flush = next((r for r in doc.custom_flush_schedule
	              if cint(r.flush_number) == cint(flush_number)), None)
	if not flush:
		frappe.throw(_("Flush {0} is not on cycle {1}.").format(flush_number, cycle))
	# The future-date and negative-stems rules live on the controller, so the desk
	# form and this endpoint cannot disagree about what a valid harvest is.
	total = cint(actual_stems)
	splits = frappe.parse_json(grade_actuals) if isinstance(grade_actuals, str) \
		else (grade_actuals or None)
	if splits:
		summed = sum(cint(v) for v in splits.values())
		if summed != total:
			frappe.throw(_(
				"The grade split adds up to {0} but the flush total is {1}. They have "
				"to agree."
			).format(summed, total))
		for r in doc.custom_weekly_targets:
			if cint(r.flush_number) == cint(flush_number) and r.grade in splits:
				r.actual_stems = cint(splits[r.grade])

	result = doc.record_harvest(flush_number, total, harvest_date)
	frappe.db.commit()
	result["cycle"] = doc.name
	result["recorded"] = total
	return result


@frappe.whitelist()
def set_actual_pinch(cycle, actual_pinch_date):
	"""Record the real pinch date, which re-anchors every date after it."""
	doc = _cycle(cycle)
	before = str(doc.custom_planned_uproot_date or "")
	doc.custom_actual_pinch_date = getdate(actual_pinch_date)
	doc.save()
	frappe.db.commit()
	return {
		"actual_pinch": str(doc.custom_actual_pinch_date),
		"planned_uproot_was": before,
		"planned_uproot_now": str(doc.custom_planned_uproot_date or ""),
		"next_harvest": str(doc.custom_next_harvest_date or ""),
		"flushes": len(doc.custom_flush_schedule),
	}


@frappe.whitelist()
def record_cuttings_on(cycle, quantity, taken_on=None):
	"""Cuttings taken off a standing cycle for propagation.

	Separate from act() because act() deliberately passes no arguments other than
	a rejection reason, and this needs a quantity.
	"""
	doc = _cycle(cycle)
	total = doc.record_cuttings(quantity, taken_on)
	frappe.db.commit()
	return {"cuttings_taken": cint(total),
	        "live_plants": cint(doc.custom_live_plant_count)}


@frappe.whitelist()
def end_cycle_on(cycle, actual_uproot_date, reason=None):
	"""Record the actual uprooting and recalculate the affected metrics."""
	doc = _cycle(cycle)
	out = doc.end_cycle(actual_uproot_date, reason)
	frappe.db.commit()
	return out


@frappe.whitelist()
def set_weekly_target(row, target_stems=None, reset=None):
	"""Override one week-and-grade target by hand, or reset it to the expectation.

	Setting a target marks the row as hand-set so later protocol changes leave it
	alone; resetting clears that mark and hands the row back to the forecast.
	"""
	_guard()
	parent = frappe.db.get_value("Crop Cycle Weekly Target", row, "parent")
	if not parent:
		frappe.throw(_("Target row {0} no longer exists.").format(row))
	doc = _cycle(parent)
	hit = next((r for r in doc.custom_weekly_targets if r.name == row), None)
	if not hit:
		frappe.throw(_("Target row {0} is not on {1}.").format(row, parent))
	if reset:
		hit.target_stems = cint(hit.expected_stems)
		hit.is_manual = 0
	else:
		if cint(target_stems) < 0:
			frappe.throw(_("A target cannot be negative."))
		hit.target_stems = cint(target_stems)
		hit.is_manual = 1
	doc.save()
	frappe.db.commit()
	return {"target_total": cint(doc.custom_target_total_stems),
	        "targets_status": doc.custom_targets_status,
	        "target_stems": cint(hit.target_stems),
	        "is_manual": cint(hit.is_manual)}


@frappe.whitelist()
def reset_targets(cycle):
	"""Hand every target back to the forecast."""
	doc = _cycle(cycle)
	n = 0
	for r in doc.custom_weekly_targets:
		if cint(r.is_manual) or cint(r.target_stems) != cint(r.expected_stems):
			r.is_manual = 0
			r.target_stems = cint(r.expected_stems)
			n += 1
	doc.save()
	frappe.db.commit()
	return {"reset": n, "target_total": cint(doc.custom_target_total_stems),
	        "targets_status": doc.custom_targets_status}


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

@frappe.whitelist()
def review_queue():
	"""Everything across the module that is waiting on a person.

	Documents in a pending state plus cycles carrying an unapproved override or
	uprooting deviation, which are not separate documents but still need a
	decision.
	"""
	_guard()
	items = []
	for doctype, field, pending, label, extra in REVIEW_SOURCES:
		if not frappe.db.table_exists(doctype):
			continue
		try:
			rows = frappe.get_all(
				doctype, filters={field: pending},
				fields=["name", "modified", "owner", field] + extra)
		except Exception:
			# A doctype that is not installed on this site must not break the queue.
			continue
		for r in rows:
			items.append({
				"doctype": doctype, "label": label, "name": r.name,
				"status": r.get(field), "modified": str(r.modified),
				"owner": r.owner,
				"detail": {k: (str(r.get(k)) if r.get(k) is not None else None)
				           for k in extra},
				"actions": ["approve", "reject"],
			})

	# Material Request is the input order sheet, and submitting it is the approval,
	# so a draft block-scoped request is what is waiting on someone. It is listed
	# rather than actioned here: submitting a Material Request goes through
	# Material Request, which is where its own validation and notifications live.
	for r in frappe.get_all(
		"Material Request",
		filters={"custom_sf_block": ["is", "set"], "docstatus": 0},
		fields=["name", "modified", "owner", "status", "custom_sf_block",
		        "custom_request_type", "schedule_date", "custom_sf_area_ha",
		        "custom_farm"]):
		items.append({
			"doctype": "Material Request", "label": "Input Request",
			"name": r.name, "status": r.status or "Draft",
			"modified": str(r.modified), "owner": r.owner,
			"detail": {"block": r.custom_sf_block,
			           "request type": r.custom_request_type,
			           "needed by": str(r.schedule_date or ""),
			           "area ha": r.custom_sf_area_ha},
			"actions": [], "route": "/app/material-request/" + r.name,
		})

	cycles = frappe.get_all(
		"Crop Cycle",
		filters={"custom_is_summer_flower_cycle": 1},
		fields=["name", "custom_block", "custom_sf_variety", "modified",
		        "custom_has_protocol_override", "custom_override_approved_by",
		        "custom_override_justification", "custom_uproot_deviation_days",
		        "custom_uproot_approved_by", "custom_uproot_deviation_reason",
		        "custom_targets_status", "custom_target_total_stems"])
	for c in cycles:
		base = {"doctype": "Crop Cycle", "name": c.name, "modified": str(c.modified),
		        "owner": None}
		if cint(c.custom_has_protocol_override) and not c.custom_override_approved_by:
			items.append({**base, "label": "Protocol Override",
			              "status": "Pending Approval",
			              "detail": {"block": c.custom_block,
			                         "variety": c.custom_sf_variety,
			                         "justification": c.custom_override_justification},
			              "actions": ["approve_protocol_override"]})
		if cint(c.custom_uproot_deviation_days) and not c.custom_uproot_approved_by:
			items.append({**base, "label": "Uproot Deviation",
			              "status": "Pending Approval",
			              "detail": {"block": c.custom_block,
			                         "deviation_days": c.custom_uproot_deviation_days,
			                         "reason": c.custom_uproot_deviation_reason},
			              "actions": ["approve_uproot_deviation"]})
		if c.custom_targets_status == "Pending Validation":
			items.append({**base, "label": "Weekly Targets",
			              "status": "Pending Validation",
			              "detail": {"block": c.custom_block,
			                         "variety": c.custom_sf_variety,
			                         "target_stems": c.custom_target_total_stems},
			              "actions": ["validate_targets", "reject_targets"]})

	items.sort(key=lambda x: x["modified"], reverse=True)
	by_label = {}
	for i in items:
		by_label[i["label"]] = by_label.get(i["label"], 0) + 1
	return {
		"items": items,
		"totals": {"pending": len(items), "by_label": by_label},
		"can_approve": bool(
			{"Farm Manager", "Agriculture Manager", "System Manager",
			 "Production Manager"} & set(frappe.get_roles(frappe.session.user))),
	}


@frappe.whitelist()
def act(doctype, name, action, reason=None):
	"""Run one whitelisted controller action.

	The doctype and action are checked against ALLOWED_ACTIONS rather than being
	trusted, so this cannot be used to reach an arbitrary method. The controller
	does the role and state checking, which is why the dashboard cannot approve
	anything the desk form would refuse.
	"""
	_guard()
	allowed = ALLOWED_ACTIONS.get(doctype)
	if not allowed or action not in allowed:
		frappe.throw(_("{0} is not an action available on {1}.").format(action, doctype))
	doc = frappe.get_doc(doctype, name)
	if doctype == "Crop Cycle" and not doc.get("custom_is_summer_flower_cycle"):
		frappe.throw(_("{0} is not a summer flower cycle.").format(name))
	fn = getattr(doc, action, None)
	if not callable(fn):
		frappe.throw(_("{0} has no {1} method.").format(doctype, action))
	out = fn(reason) if action in ("reject", "reject_targets") else fn()
	frappe.db.commit()
	return {"doctype": doctype, "name": name, "action": action,
	        "result": out if isinstance(out, (str, int, float, dict, list)) else None,
	        "status": doc.get("status") or doc.get("custom_targets_status")
	                  or doc.get("workflow_state")}


# ---------------------------------------------------------------------------
# The chain: demand -> production plan -> propagation plan -> budget
# ---------------------------------------------------------------------------

@frappe.whitelist()
def chain_status(variety=None, farm=None, demand=None):
	"""Where a variety has got to along the chain, and what the next step is.

	One call rather than four, so the dashboard cannot show a plan from one
	variety next to a budget from another.
	"""
	_guard()
	if not demand:
		f = {}
		if variety:
			f["variety"] = variety
		if farm:
			f["farm"] = farm
		rows = frappe.get_all("Summer Flower Market Demand", filters=f,
		                      pluck="name", order_by="modified desc", limit=1)
		demand = rows[0] if rows else None
	if not demand:
		return {"demand": None}

	d = frappe.db.get_value(
		"Summer Flower Market Demand", demand,
		["name", "variety", "farm", "weeks_covered", "total_demand_stems",
		 "horizon_start", "horizon_end", "horizon_status", "price_per_stem",
		 "currency"], as_dict=True)

	plans = frappe.get_all(
		"Summer Flower Production Plan", filters={"market_demand": demand,
		                                          "docstatus": ["<", 2]},
		fields=["name", "workflow_state", "docstatus", "budget", "weeks_covered",
		        "total_demand_stems", "total_production_stems", "coverage_pct",
		        "weeks_in_deficit", "new_beds_required", "new_plants_required",
		        "average_area_ha", "peak_weekly_sticking", "peak_sticking_week"],
		order_by="creation desc")
	plan = plans[0] if plans else None

	prop = None
	if plan:
		pr = frappe.get_all(
			"Summer Flower Propagation Plan",
			filters={"production_plan": plan["name"]},
			fields=["name", "status", "total_cuttings_required",
			        "peak_weekly_cuttings", "peak_week", "cuttings_from_existing",
			        "existing_cover_pct", "mother_plants_required",
			        "peak_bench_sqm", "tc_plants_required", "tc_order_date",
			        "first_sticking_date", "total_cost", "schedule_warning",
			        "motherstock_batches_created", "seedling_requests_created"],
			order_by="creation desc", limit=1)
		prop = pr[0] if pr else None

	budget = None
	if plan and plan.get("budget"):
		budget = frappe.db.get_value(
			"Summer Flower Budget", plan["budget"],
			["name", "budget_status", "total_stems", "total_value", "currency",
			 "months_covered"], as_dict=True)

	# Space: what the plan wants against what the farm actually has.
	space = None
	if plan:
		space = _space_for(plan, d.get("farm"))

	steps = [
		{"key": "demand", "label": _("Market demand"), "done": True,
		 "name": d["name"], "next": None},
		{"key": "plan", "label": _("Production plan"), "done": bool(plan),
		 "name": plan["name"] if plan else None,
		 "state": plan["workflow_state"] if plan else None,
		 "next": None if plan else "create_plan"},
		{"key": "propagation", "label": _("Propagation plan"),
		 "done": bool(prop), "name": prop["name"] if prop else None,
		 "state": prop["status"] if prop else None,
		 "next": ("create_propagation" if plan and not prop else None)},
		{"key": "budget", "label": _("Budget"), "done": bool(budget),
		 "name": budget["name"] if budget else None,
		 "state": budget["budget_status"] if budget else None,
		 "next": ("approve_plan" if plan and not budget
		          and plan["docstatus"] == 0 else None)},
	]
	return {"demand": d, "plan": plan, "propagation": prop, "budget": budget,
	        "space": space, "steps": steps}


def _space_for(plan, farm):
	"""Beds the plan wants against beds and blocks the farm has.

	Beds are rarely the binding constraint; blocks are, because a block holds one
	planting at a time and a planting sits in it for its whole life.
	"""
	blocks = frappe.get_all(
		"Block", filters={"custom_is_summer_flower_block": 1, "farm": farm},
		fields=["name", "custom_total_beds", "custom_gross_area_ha"])
	beds_have = sum(cint(b.custom_total_beds) for b in blocks)
	rows = frappe.get_all(
		"Summer Flower Plan Block",
		filters={"parent": plan["name"], "is_new_planting": 1},
		fields=["beds", "plants", "block", "below_minimum", "gross_area_ha"])
	unallocated = len([r for r in rows if not r.block])
	return {
		"blocks": len(blocks),
		"beds_available": beds_have,
		"gross_area_ha": round(sum(flt(b.custom_gross_area_ha) for b in blocks), 3),
		"beds_wanted": cint(plan.get("new_beds_required")),
		"plants_wanted": cint(plan.get("new_plants_required")),
		"area_standing_ha": flt(plan.get("average_area_ha")),
		"proposals": len(rows),
		"unallocated": unallocated,
		"below_minimum": len([r for r in rows if cint(r.below_minimum)]),
		"beds_pct": round(cint(plan.get("new_beds_required")) * 100.0 / beds_have, 1)
		            if beds_have else 0,
	}


@frappe.whitelist()
def create_production_plan(demand):
	"""Build the production plan for a demand register."""
	_guard()
	d = frappe.get_doc("Summer Flower Market Demand", demand)
	name = d.create_production_plan()
	frappe.db.commit()
	p = frappe.db.get_value("Summer Flower Production Plan", name,
	                        ["name", "weeks_covered", "coverage_pct",
	                         "new_beds_required", "weeks_in_deficit"], as_dict=True)
	return p


@frappe.whitelist()
def create_propagation_plan(plan):
	"""Work out how the plan's cuttings get sourced."""
	_guard()
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	name = p.create_propagation_plan()
	frappe.db.commit()
	return frappe.db.get_value(
		"Summer Flower Propagation Plan", name,
		["name", "status", "total_cuttings_required", "peak_weekly_cuttings",
		 "mother_plants_required", "tc_plants_required", "peak_bench_sqm"],
		as_dict=True)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

@frappe.whitelist()
def budget_detail(plan=None, budget=None):
	"""The budget a plan produced, with its months and fiscal-year postings.

	A budget only exists once a plan is approved -- approval is the transition that
	submits the plan and builds it -- so a draft plan legitimately has none. The
	payload says which case it is rather than returning an empty object, because
	"no budget yet" and "budget missing" need different actions from the user.
	"""
	_guard()
	if not budget:
		if not plan:
			return {"plan": None, "budget": None, "reason": "no plan in scope"}
		p = frappe.db.get_value(
			"Summer Flower Production Plan", plan,
			["name", "budget", "workflow_state", "status", "docstatus",
			 "total_production_stems", "price_per_stem", "currency"], as_dict=True)
		if not p:
			return {"plan": plan, "budget": None, "reason": "plan not found"}
		if not p.budget:
			return {
				"plan": plan, "budget": None,
				"plan_state": p.workflow_state or p.status,
				"submitted": p.docstatus == 1,
				"reason": "approved but no budget" if p.docstatus == 1
				          else "plan is not approved yet",
				"expected_value": flt(p.total_production_stems) * flt(p.price_per_stem),
				"currency": p.currency,
			}
		budget = p.budget

	b = frappe.get_doc("Summer Flower Budget", budget)
	fys = []
	for r in b.fiscal_years:
		md = None
		if r.monthly_distribution and frappe.db.exists("Monthly Distribution",
		                                              r.monthly_distribution):
			d = frappe.get_doc("Monthly Distribution", r.monthly_distribution)
			md = {"name": d.name, "months": len(d.percentages),
			      "pct_total": round(sum(flt(x.percentage_allocation)
			                             for x in d.percentages), 4)}
		eb = None
		if r.erpnext_budget and frappe.db.exists("Budget", r.erpnext_budget):
			e = frappe.get_doc("Budget", r.erpnext_budget)
			eb = {"name": e.name, "docstatus": e.docstatus,
			      "against": e.budget_against,
			      "against_value": e.get(frappe.scrub(e.budget_against or "")),
			      "account": e.get("account"),
			      "amount": flt(e.get("budget_amount")),
			      "rows": len(e.get("budget_distribution") or [])}
		fys.append({
			"fiscal_year": r.fiscal_year, "stems": cint(r.stems),
			"value": flt(r.value), "months_covered": cint(r.months_covered),
			"monthly_distribution": md, "erpnext_budget": eb,
		})

	return {
		"plan": b.production_plan, "budget": b.name,
		"status": b.budget_status, "currency": b.currency,
		"total_stems": cint(b.total_stems), "total_value": flt(b.total_value),
		"months_covered": cint(b.months_covered),
		"price_per_stem": flt(b.price_per_stem),
		"account": b.budget_account, "cost_center": b.cost_center,
		"variety": b.variety, "farm": b.farm,
		"posted": b.budget_status == "Posted to Accounts",
		"months": [{
			"year": m.year, "month": m.month, "month_name": m.month_name,
			"fiscal_year": m.fiscal_year, "stems": cint(m.stems),
			"value": flt(m.value), "pct_of_fiscal_year": flt(m.pct_of_fiscal_year),
		} for m in b.budget_months],
		"fiscal_years": fys,
	}


# ---------------------------------------------------------------------------
# Plan-level workflow, so the dashboard can drive the demand -> budget chain
# ---------------------------------------------------------------------------

@frappe.whitelist()
def plan_action(plan, action):
	"""Apply a workflow transition to a Production Plan."""
	_guard()
	from frappe.model.workflow import apply_workflow, get_transitions
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	available = [t.action for t in get_transitions(doc)]
	if action not in available:
		frappe.throw(_("{0} is not available on {1}. Available: {2}").format(
			action, plan, ", ".join(available) or _("none")))
	doc = apply_workflow(doc, action)
	frappe.db.commit()
	return {"plan": doc.name, "state": doc.workflow_state,
	        "budget": doc.get("budget"),
	        "transitions": [t.action for t in get_transitions(doc)]}


@frappe.whitelist()
def plan_transitions(plan):
	_guard()
	from frappe.model.workflow import get_transitions
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	return {"plan": doc.name, "state": doc.workflow_state,
	        "budget": doc.get("budget"),
	        "transitions": [t.action for t in get_transitions(doc)]}
