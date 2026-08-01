# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Write endpoints for the crop cycle dashboard.

crop_cycle_api reads. Everything here writes, and every one of them loads the
document and calls its controller, so the dashboard is held to the same rules as
the desk form.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate


def _guard():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)


@frappe.whitelist()
def create_bed_work(crop_cycle, work_type, from_bed, to_bed, reason=None,
                    new_variety=None, plants_per_bed=None):
	"""Open an uprooting or replanting job over a run of beds."""
	_guard()
	d = frappe.new_doc("Bed Work Order")
	d.crop_cycle = crop_cycle
	d.work_type = work_type
	d.from_bed = cint(from_bed)
	d.to_bed = cint(to_bed)
	d.reason = reason
	d.new_variety = new_variety
	if plants_per_bed:
		d.plants_per_bed = flt(plants_per_bed)
	d.flags.ignore_permissions = True
	d.insert()
	frappe.db.commit()
	return {"job": d.name, "total_beds": flt(d.total_beds),
	        "plants_per_bed": flt(d.plants_per_bed), "status": d.status}


@frappe.whitelist()
def log_bed_progress(job, beds_done, work_date=None, workers=None,
                     labour_hours=None, notes=None):
	"""Record a day's work on a job. Fractions are the point."""
	_guard()
	d = frappe.get_doc("Bed Work Order", job)
	out = d.log_progress(beds_done, work_date=work_date, workers=workers,
	                     labour_hours=labour_hours, notes=notes)
	frappe.db.commit()
	out["job"] = d.name
	return out


@frappe.whitelist()
def bed_work_action(job, action, reason=None):
	"""Pause, resume or cancel a job."""
	_guard()
	allowed = ("pause", "resume", "cancel_job")
	if action not in allowed:
		frappe.throw(_("{0} is not an action on a bed work order.").format(action))
	d = frappe.get_doc("Bed Work Order", job)
	out = getattr(d, action)(reason) if action in ("pause", "cancel_job") \
		else getattr(d, action)()
	frappe.db.commit()
	return {"job": d.name, "status": out}


@frappe.whitelist()
def set_biological_asset(cycle, cost_per_plant=None, create=None):
	"""Flag a cycle as a biological asset and optionally raise the Asset."""
	_guard()
	d = frappe.get_doc("Crop Cycle", cycle)
	d.custom_is_biological_asset = 1
	if cost_per_plant is not None:
		d.custom_cost_per_plant = flt(cost_per_plant)
	d.flags.ignore_permissions = True
	d.save()
	name = d.get("custom_biological_asset")
	if create and not name:
		name = d.create_asset()
	frappe.db.commit()
	d.reload()
	return {"cycle": d.name, "asset": d.get("custom_biological_asset"),
	        "value": flt(d.get("custom_biological_asset_value")),
	        "cost_per_plant": flt(d.get("custom_cost_per_plant"))}


@frappe.whitelist()
def set_cycle_scope(cycle, cycle_scope, block=None):
	"""Move a cycle between greenhouse and block scope."""
	_guard()
	if cycle_scope not in ("Greenhouse", "Block"):
		frappe.throw(_("Scope is either Greenhouse or Block."))
	d = frappe.get_doc("Crop Cycle", cycle)
	d.custom_cycle_scope = cycle_scope
	if cycle_scope == "Block" and block:
		d.custom_block = block
	d.flags.ignore_permissions = True
	d.save()
	frappe.db.commit()
	return {"cycle": d.name, "scope": d.custom_cycle_scope,
	        "block": d.get("custom_block")}
