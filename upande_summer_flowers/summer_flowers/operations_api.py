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
			"block_area_sqm": flt(doc.custom_block_area_sqm),
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
def chain_status(variety=None, farm=None, demand=None, plan=None):
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
		        "average_area_ha", "peak_weekly_sticking", "peak_sticking_week",
		        "peak_concurrent_beds", "peak_beds_week", "plantings_not_placed",
		        "unmet_stems", "blocks_used"],
		order_by="creation desc")
	# Describe the plan the dashboard is showing. Falling back to the newest is
	# what made the chain card name a draft while every tile beside it described
	# the approved plan the scope bar had selected.
	wanted = plan
	plan = next((p for p in plans if p["name"] == wanted), None) \
		if wanted else (plans[0] if plans else None)
	if plan is None and plans:
		plan = plans[0]

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


@frappe.whitelist()
def process_overview(variety=None, farm=None, plan=None):
	"""The whole process on one screen: every stage, its documents, and what stops it.

	The tabs each answer one question well and none of them answer "where is this
	crop". This walks the chain the same way it actually runs -- register, plan,
	budget, propagation, TC order, motherstock, plantings, cycles, harvest, inputs
	-- reads the documents at each stage, and says which stage is the one holding
	everything else up. Read-only.
	"""
	_guard()
	from upande_summer_flowers.summer_flowers.planning_api import resolve_plan

	today = getdate(nowdate())
	stages = []
	blockers = []

	def stage(key, label, doc=None, state=None, value=None, detail=None,
	          done=False, blocked=None, count=None, note=None):
		# blocked stops the chain; note is something to do next that does not.
		# Plantings waiting on a block used to be a blocker, and it is not one: the
		# plan is built, the TC order is sized and propagation follows from them.
		stages.append({
			"key": key, "label": label, "doc": doc, "state": state,
			"value": value, "detail": detail, "done": bool(done),
			"blocked": blocked, "note": note, "count": count,
		})
		if blocked:
			blockers.append("%s: %s" % (label, blocked))

	# ── 1. the register
	f = {}
	if variety:
		f["variety"] = variety
	# No farm filter: the register is per variety.
	dem = frappe.get_all("Summer Flower Market Demand", filters=f,
	                     fields=["name", "variety", "farm", "weeks_covered",
	                             "total_demand_stems", "horizon_start", "horizon_end",
	                             "horizon_status", "firm_demand_stems"],
	                     order_by="modified desc", limit=1)
	if not dem:
		stage("demand", "Market demand", blocked=_("No demand register in scope."))
		return {"variety": variety, "farm": farm, "stages": stages,
		        "blockers": blockers, "verdict": _("Nothing to show: no demand register.")}
	d = dem[0]
	# The register names the variety; the farm comes from the plan, since one
	# variety's demand can be met from several farms.
	variety = d.variety
	stage("demand", "Market demand", d.name, d.horizon_status,
	      "{:,}".format(cint(d.total_demand_stems)) + _(" stems"),
	      _("{0} weeks, {1} to {2}").format(d.weeks_covered, d.horizon_start,
	                                        d.horizon_end),
	      done=True)

	# ── 2. the plan
	plan = resolve_plan(variety, farm, plan)
	p = frappe.get_doc("Summer Flower Production Plan", plan) if plan else None
	if p and not farm:
		farm = p.farm
	if not p:
		stage("plan", "Production plan",
		      blocked=_("No plan yet. Create one from the register."))
	else:
		short = cint(p.plantings_not_placed)
		stage("plan", "Production plan", p.name, p.workflow_state or p.status,
		      "%.1f%%" % flt(p.coverage_pct) + _(" of demand"),
		      (_("{0} plantings, {1} beds standing at the peak").format(
			      len([b for b in p.plan_blocks if b.is_new_planting]),
			      cint(p.peak_concurrent_beds))
		       if cint(p.peak_concurrent_beds)
		       # Plans built before the peak was computed have it at zero. Quote the
		       # horizon total instead of printing "0 beds", which reads as an error.
		       else _("{0} plantings, {1} beds over the horizon").format(
			      len([b for b in p.plan_blocks if b.is_new_planting]),
			      cint(p.new_beds_required))),
		      done=p.docstatus == 1,
		      # Not "blocked": the plan is built and the propagation and TC figures
		      # follow from it. Assigning blocks is the next job, not a prerequisite.
		      blocked=None,
		      note=(_("{0} plantings are waiting on a block ({1} stems). Assign blocks "
		              "before planting; the plan and the TC order already include them."
		              ).format(short, "{:,}".format(cint(p.unmet_stems)))
		            if short else None))

	# ── 3. the budget
	budget = p.get("budget") if p else None
	if budget:
		b = frappe.db.get_value("Summer Flower Budget", budget,
		                        ["budget_status", "total_value", "currency",
		                         "months_covered"], as_dict=True)
		stage("budget", "Budget", budget, b.budget_status,
		      "{:,}".format(cint(b.total_value)) + " " + (b.currency or ""),
		      _("{0} months").format(b.months_covered),
		      done=b.budget_status in ("Posted to Accounts", "Approved"))
	else:
		stage("budget", "Budget", state=_("not posted"),
		      detail=_("Approving the plan posts it"),
		      blocked=(_("The plan is not approved, so no budget exists.")
		               if p and p.docstatus != 1 else None))

	# ── 4. propagation
	pr = None
	if p:
		rows = frappe.get_all("Summer Flower Propagation Plan",
		                      filters={"production_plan": p.name,
		                               "status": ["!=", "Rejected"]},
		                      fields=["name", "status", "total_cuttings_required",
		                              "cuttings_uncovered", "plants_short",
		                              "stems_at_risk", "tc_plants_required",
		                              "tc_order_date", "tc_cost", "currency",
		                              "mother_plants_required", "peak_bench_sqm",
		                              "first_sticking_date", "full_capacity_date",
		                              "ramp_weeks", "motherstock_batches_created",
		                              "seedling_requests_created"],
		                      order_by="creation desc", limit=1)
		pr = rows[0] if rows else None
	if not pr:
		stage("propagation", "Propagation plan",
		      blocked=_("Not created, so nothing knows where the cuttings come from."))
	else:
		stage("propagation", "Propagation plan", pr.name, pr.status,
		      "{:,}".format(cint(pr.total_cuttings_required)) + _(" cuttings"),
		      _("{0} mother plants on {1} m² of bench").format(
			      cint(pr.mother_plants_required), flt(pr.peak_bench_sqm)),
		      done=pr.status == "Approved",
		      blocked=(_("{0} cuttings uncovered — {1} plants never stuck, about {2} "
		                 "stems the plan still counts.").format(
			                 "{:,}".format(cint(pr.cuttings_uncovered)),
			                 "{:,}".format(cint(pr.plants_short)),
			                 "{:,}".format(cint(pr.stems_at_risk)))
		               if cint(pr.cuttings_uncovered) else None))

		late = pr.tc_order_date and getdate(pr.tc_order_date) < today
		stage("tc", "TC order", pr.name,
		      _("overdue") if late else _("to place"),
		      "{:,}".format(cint(pr.tc_plants_required)) + _(" plantlets"),
		      _("order by {0}, {1} {2}").format(
			      pr.tc_order_date, "{:,}".format(cint(pr.tc_cost)),
			      pr.currency or ""),
		      done=bool(frappe.db.count("Summer Flower Motherstock Batch",
		                                {"production_plan": p.name,
		                                 "docstatus": ["<", 2]})),
		      blocked=(_("The order date {0} passed {1} days ago. This is the longest "
		                 "lead time in the process — nothing downstream moves until it "
		                 "is placed or the cuttings are bought in rooted.").format(
			                 pr.tc_order_date,
			                 (today - getdate(pr.tc_order_date)).days)
		               if late else None))

	# ── 5. motherstock on the bench
	batches = frappe.get_all("Summer Flower Motherstock Batch",
	                         filters={"variety": variety, "farm": farm,
	                                  "docstatus": ["<", 2]},
	                         fields=["name", "batch_status", "mother_plants",
	                                 "first_sticking_date", "max_pc_date",
	                                 "ramp_weeks", "expiry_date"],
	                         order_by="first_sticking_date asc")
	if batches:
		cutting = [b for b in batches
		           if b.first_sticking_date and getdate(b.first_sticking_date) <= today]
		stage("motherstock", "Motherstock",
		      batches[0].name if len(batches) == 1 else None,
		      _("{0} cutting now").format(len(cutting)) if cutting else _("establishing"),
		      "{:,}".format(sum(cint(b.mother_plants) for b in batches)) + _(" mothers"),
		      _("first cut {0}, full rate {1}").format(
			      batches[0].first_sticking_date, batches[0].max_pc_date),
		      done=bool(cutting), count=len(batches))
	else:
		stage("motherstock", "Motherstock",
		      blocked=_("No batch raised, so there is nothing to cut from."))

	# ── 6. plantings
	pcs = frappe.get_all("Planting Calendar",
	                     filters={"variety": variety, "farm": farm,
	                              "calendar_status": ["not in", ("Cancelled",)]},
	                     fields=["name", "calendar_status", "block", "plants",
	                             "crop_cycle", "planting_date"])
	by_state = {}
	for r in pcs:
		by_state[r.calendar_status] = by_state.get(r.calendar_status, 0) + 1
	if pcs:
		stage("plantings", "Planting calendar", None,
		      ", ".join("%s %s" % (v, k.lower()) for k, v in sorted(by_state.items())),
		      "%s" % len(pcs) + _(" plantings"),
		      "{:,}".format(sum(cint(r.plants) for r in pcs)) + _(" plants committed"),
		      done=True, count=len(pcs))
	else:
		stage("plantings", "Planting calendar",
		      blocked=_("None created. An approved plan can create them."))

	# ── 7. cycles on the ground
	cycles = frappe.get_all("Crop Cycle",
	                        filters={"custom_is_summer_flower_cycle": 1,
	                                 "custom_sf_variety": variety},
	                        fields=["name", "custom_sf_cycle_status", "custom_block",
	                                "custom_live_plant_count",
	                                "custom_total_harvested_stems",
	                                "custom_expected_stems_life",
	                                "custom_next_harvest_date"]) 		if frappe.get_meta("Crop Cycle").has_field("custom_sf_variety") else []
	if cycles:
		stage("cycles", "Crop cycles", None,
		      ", ".join(sorted({c.custom_sf_cycle_status or "?" for c in cycles})),
		      "%s" % len(cycles) + _(" on the ground"),
		      "{:,}".format(sum(cint(c.custom_live_plant_count) for c in cycles))
		      + _(" plants standing"),
		      done=True, count=len(cycles))
	else:
		stage("cycles", "Crop cycles",
		      blocked=_("None. An approved planting calendar creates the cycle."))

	# ── 8. harvest
	child = frappe.get_meta("Crop Cycle").get_field("custom_flush_schedule")
	flushes = []
	if cycles and child:
		flushes = frappe.get_all(child.options,
		                         filters={"parent": ["in", [c.name for c in cycles]]},
		                         fields=["parent", "flush_number", "harvest_date",
		                                 "expected_stems", "actual_stems",
		                                 "is_harvested"])
	due = [f for f in flushes if f.harvest_date and getdate(f.harvest_date) <= today]
	open_due = [f for f in due if not cint(f.is_harvested)]
	cut = sum(cint(c.custom_total_harvested_stems) for c in cycles)
	if flushes:
		stage("harvest", "Harvest", None,
		      _("{0} flushes due, {1} still to record").format(len(due), len(open_due)),
		      "{:,}".format(cut) + _(" stems cut"),
		      _("of {0} expected over the cycles' lives").format(
			      "{:,}".format(sum(cint(c.custom_expected_stems_life)
			                        for c in cycles))),
		      done=bool(cut),
		      blocked=(_("{0} flushes have come due with nothing recorded against "
		                 "them.").format(len(open_due)) if open_due else None))
	else:
		stage("harvest", "Harvest",
		      blocked=_("No flush schedule yet — nothing to harvest against."))

	# ── 9. inputs
	blocks = [c.custom_block for c in cycles if c.custom_block]
	mrs = frappe.get_all("Material Request",
	                     filters={"custom_sf_block": ["in", blocks or [""]],
	                              "docstatus": ["<", 2]},
	                     fields=["name", "status", "docstatus"]) if blocks else []
	if mrs:
		stage("inputs", "Input requests", None,
		      _("{0} draft, {1} submitted").format(
			      len([m for m in mrs if m.docstatus == 0]),
			      len([m for m in mrs if m.docstatus == 1])),
		      "%s" % len(mrs) + _(" material requests"),
		      _("chemicals and fertiliser for the standing blocks"),
		      done=any(m.docstatus == 1 for m in mrs), count=len(mrs))
	else:
		stage("inputs", "Input requests", state=_("none raised"),
		      detail=_("Raised per block from the crop cycle"))

	# The one thing worth acting on: the earliest stage that is stuck.
	stuck = next((s for s in stages if s["blocked"]), None)
	verdict = (_("{0} is what to deal with first. {1}").format(stuck["label"],
	                                                           stuck["blocked"])
	           if stuck else
	           _("Every stage from the register to the field has what it needs."))
	return {
		"variety": variety, "farm": farm, "plan": plan,
		"stages": stages, "blockers": blockers, "verdict": verdict,
		"stuck": stuck["key"] if stuck else None,
		"done_count": len([s for s in stages if s["done"]]),
		"total_count": len(stages),
	}


def _space_for(plan, farm):
	"""Beds the plan wants against beds and blocks the farm has.

	Beds are rarely the binding constraint; blocks are, because a block holds one
	planting at a time and a planting sits in it for its whole life.
	"""
	blocks = frappe.get_all(
		"Block", filters={"custom_is_summer_flower_block": 1, "farm": farm},
		fields=["name", "custom_total_beds", "custom_net_area_ha"])
	beds_have = sum(cint(b.custom_total_beds) for b in blocks)
	rows = frappe.get_all(
		"Summer Flower Plan Block",
		filters={"parent": plan["name"], "is_new_planting": 1},
		fields=["beds", "plants", "block", "below_minimum", "net_area_ha"])
	unallocated = len([r for r in rows if not r.block])
	peak = cint(plan.get("peak_concurrent_beds"))
	return {
		"blocks": len(blocks),
		"beds_available": beds_have,
		"net_area_ha": round(sum(flt(b.custom_net_area_ha) for b in blocks), 3),
		"beds_wanted": cint(plan.get("new_beds_required")),
		"plants_wanted": cint(plan.get("new_plants_required")),
		"area_standing_ha": flt(plan.get("average_area_ha")),
		"proposals": len(rows),
		"placed": len(rows) - unallocated,
		"unallocated": unallocated,
		"below_minimum": len([r for r in rows if cint(r.below_minimum)]),
		"blocks_used": cint(plan.get("blocks_used")),
		"unmet_stems": cint(plan.get("unmet_stems")),
		# Beds over the horizon double-count a block used twice, so the pressure
		# figure is the most beds standing at once.
		"peak_beds": peak,
		"peak_beds_week": plan.get("peak_beds_week"),
		"beds_pct": round(peak * 100.0 / beds_have, 1) if beds_have else 0,
	}


@frappe.whitelist()
def create_production_plan(demand, farm=None, season_start_year=None, force_new=0):
	"""Build the production plan for a demand register.

	An open draft is rebuilt rather than joined by another one. Pressing this a few
	times used to leave a row of identically named drafts with nothing to tell them
	apart, which is exactly the confusion this is meant to resolve: a demand has one
	plan being worked on at a time. Pass force_new to keep a draft and start a
	separate scenario beside it.
	"""
	_guard()
	if not cint(force_new):
		open_draft = frappe.get_all(
			"Summer Flower Production Plan",
			filters={"market_demand": demand, "docstatus": 0,
			         "workflow_state": ["in", ["Draft", "Rejected"]]},
			pluck="name", order_by="creation desc", limit=1)
		if open_draft:
			doc = frappe.get_doc("Summer Flower Production Plan", open_draft[0])
			doc.regenerate()
			frappe.db.commit()
			doc.reload()
			return {"name": doc.name, "weeks_covered": cint(doc.weeks_covered),
			        "coverage_pct": flt(doc.coverage_pct),
			        "new_beds_required": cint(doc.new_beds_required),
			        "weeks_in_deficit": cint(doc.weeks_in_deficit),
			        "reused": 1}

	d = frappe.get_doc("Summer Flower Market Demand", demand)
	name = d.create_production_plan(farm=farm, season_start_year=season_start_year)
	frappe.db.commit()
	p = frappe.db.get_value("Summer Flower Production Plan", name,
	                        ["name", "weeks_covered", "coverage_pct",
	                         "new_beds_required", "weeks_in_deficit"], as_dict=True)
	p["reused"] = 0
	return p


@frappe.whitelist()
def block_options(plan, row=None):
	"""The blocks that could hold each planting awaiting one. Assigns nothing."""
	_guard()
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		block_suggestions,
	)

	out = block_suggestions(plan, only_unassigned=0)
	if row:
		out["plantings"] = [p for p in out["plantings"] if p["row"] == row]
	return out


@frappe.whitelist()
def set_block(plan, row, block=None):
	"""Put one planting in one block, or clear it. The planner's decision, applied."""
	_guard()
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		assign_block,
	)

	return assign_block(plan, row, block)


@frappe.whitelist()
def assign_all_blocks(plan):
	"""Take every suggestion at once, for the plans with thirty-five plantings."""
	_guard()
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		autoassign_blocks,
	)

	return autoassign_blocks(plan)


@frappe.whitelist()
def create_propagation_plan(plan):
	"""Work out how the plan's cuttings get sourced."""
	_guard()
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	outcome = p.create_propagation_plan()
	frappe.db.commit()
	row = frappe.db.get_value(
		"Summer Flower Propagation Plan", outcome["name"],
		["name", "status", "total_cuttings_required", "peak_weekly_cuttings",
		 "mother_plants_required", "tc_plants_required", "peak_bench_sqm",
		 "season", "last_change_summary"],
		as_dict=True)
	# Created or updated, and what moved. There is one propagation plan per variety
	# per season, so pressing this on a second plan for the same crop rebuilds the
	# existing one -- silently, until now.
	row["created"] = outcome["created"]
	row["changes"] = outcome["changes"]
	return row


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
	"""Apply a workflow transition to a Production Plan, or rebuild a draft."""
	_guard()
	from frappe.model.workflow import apply_workflow, get_transitions
	doc = frappe.get_doc("Summer Flower Production Plan", plan)

	if action == "regenerate":
		# Not a workflow transition: rebuilding is an edit, and the controller
		# refuses it on anything already submitted. Exposed here so a plan can be
		# altered and re-agreed from the dashboard, which is where it is read.
		doc.regenerate()
		frappe.db.commit()
		doc.reload()
		return {"plan": doc.name, "state": doc.workflow_state,
		        "budget": doc.get("budget"),
		        "coverage_pct": flt(doc.coverage_pct),
		        "new_beds_required": cint(doc.new_beds_required),
		        "transitions": [t.action for t in get_transitions(doc)]}

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
