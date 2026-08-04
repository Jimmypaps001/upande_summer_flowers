# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Read endpoints for the crop cycle dashboard.

Scope-aware throughout. A greenhouse cycle is described by bed ranges and the
varieties grown across them; a block cycle by the block's own beds and, when it
is a summer flower cycle, a flush forecast. The dashboard shows whichever applies
rather than blanking half its panels for the other kind.

Reads only. Anything that writes lives in crop_cycle_ops.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate


def _guard():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)


def _short(block):
	return str(block or "").split(" - Block ").pop()


@frappe.whitelist()
def scope():
	"""Farms, varieties and scopes that actually have cycles."""
	_guard()
	rows = frappe.get_all("Crop Cycle", fields=["farm", "custom_sf_variety",
	                                            "custom_cycle_scope"])
	return {
		"farms": sorted({r.farm for r in rows if r.farm}),
		"varieties": sorted({r.custom_sf_variety for r in rows
		                     if r.custom_sf_variety}),
		"scopes": ["Greenhouse", "Block"],
		"statuses": ["Active", "Replanting", "Partially Uprooted", "Ended"],
	}


@frappe.whitelist()
def cycles(farm=None, variety=None, cycle_scope=None, status=None):
	"""Every cycle in scope, with the facts that differ by scope filled in."""
	_guard()
	f = {}
	if farm:
		f["farm"] = farm
	if variety:
		f["custom_sf_variety"] = variety
	if cycle_scope:
		f["custom_cycle_scope"] = cycle_scope
	if status:
		f["custom_sf_cycle_status"] = status

	rows = frappe.get_all(
		"Crop Cycle", filters=f,
		fields=["name", "custom_cycle_scope", "custom_block", "greenhouse",
		        "custom_greenhouse", "farm", "company", "custom_sf_variety",
		        "custom_is_summer_flower_cycle", "custom_sf_cycle_status",
		        "custom_planting_date", "custom_planned_uproot_date",
		        "custom_actual_uproot_date", "custom_plant_age_weeks",
		        "custom_live_plant_count", "custom_area_planted_sqm",
		        "custom_area_utilisation_pct", "custom_beds_planted",
		        "custom_current_flush_number", "custom_next_harvest_date",
		        "custom_expected_stems_next_flush", "custom_flushes_remaining",
		        "custom_expected_stems_life", "custom_total_harvested_stems",
		        "custom_harvest_variance_stems", "custom_targets_status",
		        "custom_is_biological_asset", "custom_biological_asset",
		        "custom_biological_asset_value", "custom_has_protocol_override",
		        "custom_override_approved_by", "custom_uproot_deviation_days",
		        "custom_uproot_approved_by", "custom_crop_protocol_version",
		        "number_of_beds", "area_planted", "number_of_plants", "varieties",
		        "last_replanting_date"],
		order_by="custom_cycle_scope asc, name asc")

	# Live bed-work jobs, so the list can say which cycles have work in progress.
	jobs = {}
	for j in frappe.get_all(
			"Bed Work Order",
			filters={"status": ["in", ("Not Started", "In Progress", "Paused")]},
			fields=["crop_cycle", "work_type", "status", "pct_complete", "name"]):
		jobs.setdefault(j.crop_cycle, []).append(j)

	out = []
	for r in rows:
		block_scoped = (r.custom_cycle_scope or
		                ("Block" if r.custom_block else "Greenhouse")) == "Block"
		r["is_block"] = block_scoped
		r["where"] = _short(r.custom_block) if block_scoped else (
			r.greenhouse or r.custom_greenhouse or "")
		r["location"] = r.custom_block if block_scoped else r.greenhouse
		# Plants and area come from different places depending on scope.
		r["plants"] = (cint(r.custom_live_plant_count) if block_scoped
		               else cint(r.number_of_plants))
		r["area_sqm"] = (flt(r.custom_area_planted_sqm) if block_scoped
		                 else flt(r.area_planted))
		r["beds"] = (cint(r.custom_beds_planted) if block_scoped
		             else cint(r.number_of_beds))
		r["jobs"] = jobs.get(r.name, [])
		r["needs"] = []
		if r.custom_has_protocol_override and not r.custom_override_approved_by:
			r["needs"].append("override approval")
		if cint(r.custom_uproot_deviation_days) and not r.custom_uproot_approved_by:
			r["needs"].append("uproot approval")
		if r.custom_targets_status == "Pending Validation":
			r["needs"].append("target validation")
		if r["jobs"]:
			r["needs"].append("%d job(s) open" % len(r["jobs"]))
		out.append(r)

	sf = [r for r in out if cint(r.custom_is_summer_flower_cycle)]
	due = [r for r in out if r.custom_next_harvest_date]
	return {
		"cycles": out,
		"totals": {
			"cycles": len(out),
			"block": len([r for r in out if r["is_block"]]),
			"greenhouse": len([r for r in out if not r["is_block"]]),
			"summer_flower": len(sf),
			"active": len([r for r in out
			               if r.custom_sf_cycle_status == "Active"]),
			"ended": len([r for r in out
			              if r.custom_sf_cycle_status == "Ended"]),
			"plants": sum(r["plants"] for r in out),
			"area_ha": round(sum(r["area_sqm"] for r in out) / 10_000, 3),
			"beds": sum(r["beds"] for r in out),
			"harvested": sum(cint(r.custom_total_harvested_stems) for r in out),
			"remaining_life": sum(cint(r.custom_expected_stems_life) for r in sf),
			"asset_value": sum(flt(r.custom_biological_asset_value) for r in out),
			"assets": len([r for r in out if r.custom_biological_asset]),
			"needing_attention": len([r for r in out if r["needs"]]),
			"open_jobs": sum(len(r["jobs"]) for r in out),
			"next_harvest": min((str(r.custom_next_harvest_date) for r in due),
			                    default=None),
		},
	}


@frappe.whitelist()
def cycle(name):
	"""One cycle in full, shaped by its scope."""
	_guard()
	d = frappe.get_doc("Crop Cycle", name)
	block_scoped = d.is_block_scoped() if hasattr(d, "is_block_scoped") else \
		bool(d.get("custom_block"))
	is_sf = cint(d.get("custom_is_summer_flower_cycle"))

	out = {
		"cycle": d.name,
		"scope": d.get("custom_cycle_scope") or ("Block" if block_scoped
		                                         else "Greenhouse"),
		"is_block": block_scoped,
		"is_summer_flower": bool(is_sf),
		"block": d.get("custom_block"),
		"block_code": _short(d.get("custom_block")),
		"greenhouse": d.get("greenhouse") or d.get("custom_greenhouse"),
		"farm": d.farm, "company": d.company,
		"variety": d.get("custom_sf_variety"),
		"status": d.get("custom_sf_cycle_status"),
		"protocol": d.get("custom_crop_protocol_version"),
		"planting_calendar": d.get("custom_planting_calendar"),
		"asset": {
			"is_biological": cint(d.get("custom_is_biological_asset")),
			"asset": d.get("custom_biological_asset"),
			"cost_per_plant": flt(d.get("custom_cost_per_plant")),
			"value": flt(d.get("custom_biological_asset_value")),
		},
		"logs": {
			"replanting": [{
				"date": str(r.replant_date or ""), "from_bed": r.from_bed,
				"to_bed": r.to_bed, "qty": cint(r.qty_replanted),
				"variety": r.new_variety, "cost": flt(r.cost_of_replanting),
				"remarks": r.remarks,
			} for r in d.get("replanting_logs") or []],
			"uprooting": [{
				"date": str(r.uproot_date or ""), "from_bed": r.from_bed,
				"to_bed": r.to_bed, "qty": cint(r.qty_uprooted),
				"reason": r.reason, "remarks": r.remarks,
			} for r in d.get("uprooting_logs") or []],
		},
		"jobs": bed_work(cycle=name)["jobs"],
	}

	if block_scoped:
		out["geometry"] = {
			"area_planted_sqm": flt(d.get("custom_area_planted_sqm")),
			"block_area_sqm": flt(d.get("custom_block_area_sqm")),
			"utilisation_pct": flt(d.get("custom_area_utilisation_pct")),
			"density": flt(d.get("custom_planting_density_per_sqm")),
			"beds": cint(d.get("custom_beds_planted")),
			"plants": cint(d.get("custom_live_plant_count")),
			"cuttings_taken": cint(d.get("custom_cuttings_taken")),
		}
		out["beds"] = _block_beds(d.get("custom_block"))
	else:
		out["geometry"] = {
			"area_planted_sqm": flt(d.get("area_planted")),
			"block_area_sqm": flt(d.get("net_area")),
			"utilisation_pct": (flt(d.get("area_planted")) * 100 /
			                    flt(d.get("net_area"))) if flt(d.get("net_area"))
			                   else 0,
			"density": flt(d.get("plants_per_sqm")),
			"beds": cint(d.get("number_of_beds")),
			"plants": cint(d.get("number_of_plants")),
			"cuttings_taken": 0,
		}
		out["bed_ranges"] = [{
			"from_bed": r.from_bed, "to_bed": r.to_bed, "variety": r.variety,
			"planting_date": str(r.planting_date or ""),
			"area": flt(r.total_beds_area), "protocol": r.crop_protocol,
		} for r in d.get("bed_range") or []]
		out["varieties_grown"] = [{
			"variety": r.variety, "beds": cint(r.beds), "area_m2": flt(r.area_m2),
			"plants": cint(r.plants),
		} for r in d.get("varieties_grown") or []]

	if is_sf:
		today = getdate(nowdate())
		out["dates"] = {
			"planting": str(d.get("custom_planting_date") or ""),
			"planned_pinch": str(d.get("custom_planned_pinch_date") or ""),
			"actual_pinch": str(d.get("custom_actual_pinch_date") or ""),
			"planned_uproot": str(d.get("custom_planned_uproot_date") or ""),
			"actual_uproot": str(d.get("custom_actual_uproot_date") or ""),
			"plant_age_weeks": cint(d.get("custom_plant_age_weeks")),
			"uproot_deviation_days": cint(d.get("custom_uproot_deviation_days")),
		}
		out["harvest"] = {
			"current_flush": cint(d.get("custom_current_flush_number")),
			"next_date": str(d.get("custom_next_harvest_date") or ""),
			"expected_next": cint(d.get("custom_expected_stems_next_flush")),
			"flushes_remaining": cint(d.get("custom_flushes_remaining")),
			"expected_life": cint(d.get("custom_expected_stems_life")),
			"harvested": cint(d.get("custom_total_harvested_stems")),
			"variance": cint(d.get("custom_harvest_variance_stems")),
			"remaining": cint(d.get("custom_expected_stems_remaining")),
		}
		out["flushes"] = [{
			"flush_number": r.flush_number, "harvest_date": str(r.harvest_date),
			"label": "%s-W%02d" % (r.year, r.week_no),
			"stems_per_plant": flt(r.stems_per_plant),
			"expected_stems": cint(r.expected_stems),
			"plants_standing": cint(r.plants_standing),
			"is_harvested": cint(r.is_harvested),
			"actual_stems": cint(r.actual_stems),
			"variance_stems": cint(r.variance_stems),
			"is_due": bool(r.harvest_date and getdate(r.harvest_date) <= today),
		} for r in d.get("custom_flush_schedule") or []]
		out["targets_status"] = d.get("custom_targets_status")
	return out


def _block_beds(block):
	if not block:
		return []
	rows = frappe.get_all(
		"Bed", filters={"custom_block": block},
		fields=["name", "bed", "custom_bed_status", "custom_plants",
		        "custom_uproot_date", "bed_length", "bed_width"],
		order_by="bed asc")
	return [{
		"bed": r.name, "number": r.bed, "status": r.custom_bed_status or "Empty",
		"plants": cint(r.custom_plants),
		"uproot_date": str(r.custom_uproot_date) if r.custom_uproot_date else None,
		"area_sqm": flt(r.bed_length) * flt(r.bed_width),
	} for r in rows]


@frappe.whitelist()
def bed_work(cycle=None, farm=None, status=None, work_type=None):
	"""Uprooting and replanting jobs, with where each one reached."""
	_guard()
	f = {}
	if cycle:
		f["crop_cycle"] = cycle
	if farm:
		f["farm"] = farm
	if status:
		f["status"] = status
	if work_type:
		f["work_type"] = work_type
	rows = frappe.get_all(
		"Bed Work Order", filters=f,
		fields=["name", "work_type", "crop_cycle", "block", "farm", "variety",
		        "status", "from_bed", "to_bed", "total_beds", "beds_done",
		        "beds_remaining", "pct_complete", "last_full_bed", "current_bed",
		        "fraction_in_bed", "plants_per_bed", "plants_affected",
		        "started_on", "last_worked_on", "completed_on", "days_worked",
		        "total_labour_hours", "logged_to_cycle", "reason", "new_variety"],
		order_by="modified desc")
	for r in rows:
		r["block_code"] = _short(r.block)
		r["progress"] = frappe.get_all(
			"Bed Work Progress", filters={"parent": r.name},
			fields=["name", "work_date", "beds_done", "cumulative_beds",
			        "bed_reached", "fraction_in_bed", "plants", "workers",
			        "labour_hours", "notes"],
			order_by="work_date asc, idx asc")
		# The same sentence the controller gives, so the dashboard and the form agree.
		if not flt(r.beds_done):
			r["summary"] = _("Not started.")
		elif r.status == "Completed":
			r["summary"] = _("Finished: all {0} beds ({1}-{2}) done.").format(
				flt(r.total_beds), r.from_bed, r.to_bed)
		elif cint(r.current_bed):
			r["summary"] = _(
				"Beds {0}-{1} finished, and {2}% of bed {3}. {4} of {5} beds to go."
			).format(r.from_bed, r.last_full_bed or r.from_bed,
			         flt(r.fraction_in_bed), r.current_bed, flt(r.beds_remaining),
			         flt(r.total_beds))
		else:
			r["summary"] = _("Beds {0}-{1} finished. {2} of {3} beds to go.").format(
				r.from_bed, r.last_full_bed, flt(r.beds_remaining),
				flt(r.total_beds))
	live = [r for r in rows if r.status in ("Not Started", "In Progress", "Paused")]
	return {
		"jobs": rows,
		"totals": {
			"jobs": len(rows), "open": len(live),
			"uprooting": len([r for r in rows if r.work_type == "Uprooting"]),
			"replanting": len([r for r in rows if r.work_type == "Replanting"]),
			"completed": len([r for r in rows if r.status == "Completed"]),
			"beds_outstanding": round(sum(flt(r.beds_remaining) for r in live), 2),
			"beds_done_open": round(sum(flt(r.beds_done) for r in live), 2),
			"labour_hours": round(sum(flt(r.total_labour_hours) for r in rows), 2),
			"plants_affected": sum(cint(r.plants_affected) for r in rows),
		},
	}


@frappe.whitelist()
def biological_assets(farm=None):
	"""The standing crop held as assets, and what it is worth."""
	_guard()
	f = {"custom_is_biological_asset": 1}
	if farm:
		f["farm"] = farm
	rows = frappe.get_all(
		"Crop Cycle", filters=f,
		fields=["name", "custom_block", "greenhouse", "farm", "custom_sf_variety",
		        "custom_biological_asset", "custom_biological_asset_value",
		        "custom_cost_per_plant", "custom_live_plant_count",
		        "custom_planting_date", "custom_sf_cycle_status"])
	meta = frappe.get_meta("Asset")
	value_field = next((x for x in ("purchase_amount", "net_purchase_amount",
	                                "gross_purchase_amount") if meta.has_field(x)),
	                   None)
	for r in rows:
		r["block_code"] = _short(r.custom_block) or (r.greenhouse or "")
		r["asset_status"] = None
		r["asset_value"] = None
		if r.custom_biological_asset:
			fields = ["status", "docstatus", "available_for_use_date"]
			if value_field:
				fields.append(value_field)
			a = frappe.db.get_value("Asset", r.custom_biological_asset, fields,
			                        as_dict=True)
			if a:
				r["asset_status"] = a.get("status")
				r["asset_docstatus"] = a.get("docstatus")
				r["asset_in_use_from"] = str(a.get("available_for_use_date") or "")
				r["asset_value"] = flt(a.get(value_field)) if value_field else None
	return {
		"assets": rows,
		"totals": {
			"cycles": len(rows),
			"with_asset": len([r for r in rows if r.custom_biological_asset]),
			"without_asset": len([r for r in rows if not r.custom_biological_asset]),
			"cycle_value": sum(flt(r.custom_biological_asset_value) for r in rows),
			"asset_value": sum(flt(r["asset_value"] or 0) for r in rows),
			"plants": sum(cint(r.custom_live_plant_count) for r in rows),
			"drafts": len([r for r in rows if r.get("asset_docstatus") == 0]),
		},
	}
