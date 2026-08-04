# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The one place a summer flower protocol is edited.

Crop Protocol belongs to upande_agriculture and is the variety master. The summer
flower parameters live on it as custom_sf_ fields, with nine natives reused where
they already mean the same thing -- plants per m², weeks to pinch, the flush
interval, the life totals -- so those stop reading zero for these varieties.

Crop Protocol Version is no longer edited. It is written as a read-only snapshot
when a change here is approved, and every plan, planting and crop cycle keeps
pointing at the snapshot it was built on. That is what makes "this cycle ran under
these numbers" true even after the protocol moves on.

The derivation is not reimplemented here. An unsaved Crop Protocol Version is
loaded with these values and run through its own controller, then the results are
read back. One set of maths, used by the thing being edited and the snapshot alike,
so the two cannot disagree.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

# our field name  ->  where it lives on Crop Protocol
NATIVE = {
	"farm": "farm",
	"variety": "variety",
	"plants_per_sqm_net": "plants_per_sqm",
	"weeks_to_pinch": "weeks_to_pinch",
	"flush_interval_weeks": "weeks_between_flushes",
	"total_weeks_in_ground": "total_weeks_in_ground",
	"total_flushes": "total_flushes",
	"total_stems_per_plant_life": "total_stems_per_plant_life",
	"life_expectancy_years": "life_expectancy_years",
	"stated_yield_stems_per_ha": "yield_stems_per_ha",
}
SKIP = ("Section Break", "Column Break", "Tab Break", "HTML")
# Machinery that belongs to the snapshot, never to the editable protocol.
MACHINERY = {
	"crop_protocol", "version", "version_status", "workflow_state", "effective_from",
	"effective_to", "is_current", "supersedes", "superseded_by", "change_reason",
	"approved_by", "approved_on", "naming_series",
}


def _fields():
	"""Every Crop Protocol Version field that carries protocol data."""
	return [f for f in frappe.get_meta("Crop Protocol Version").fields
	        if f.fieldtype not in SKIP and f.fieldname not in MACHINERY]


def source_field(fieldname):
	"""Where a version field's value is held on Crop Protocol."""
	return NATIVE.get(fieldname, "custom_sf_" + fieldname)


def is_summer_flower(doc):
	return bool(cint(doc.get("custom_is_summer_flower")))


def to_version(doc, version=None):
	"""Load a Crop Protocol's values onto an (unsaved) Crop Protocol Version."""
	v = version or frappe.new_doc("Crop Protocol Version")
	for f in _fields():
		src = source_field(f.fieldname)
		if f.fieldtype == "Table":
			v.set(f.fieldname, [])
			for row in (doc.get(src) or []):
				v.append(f.fieldname, {
					k: row.get(k) for k in row.as_dict()
					if k not in ("name", "parent", "parenttype", "parentfield",
					             "doctype", "idx", "owner", "creation", "modified",
					             "modified_by", "docstatus")
				})
			continue
		v.set(f.fieldname, doc.get(src))
	v.crop_protocol = doc.name
	return v


def derive(doc):
	"""Compute every derived figure by running the version's own controller."""
	if not is_summer_flower(doc):
		return
	if not doc.get("plants_per_sqm"):
		# The version controller throws without it, and a half-filled protocol
		# should be savable while it is being filled in.
		return

	probe = to_version(doc)
	probe.version = 1
	probe.effective_from = frappe.utils.nowdate()
	probe.change_reason = "derivation probe"
	probe.flags.ignore_permissions = True
	probe.flags.ignore_mandatory = True
	probe.run_method("validate")

	for f in _fields():
		if not f.read_only or f.fieldtype == "Table":
			continue
		doc.set(source_field(f.fieldname), probe.get(f.fieldname))
	# The flush rows gain their derived columns too.
	rows = {cint(r.flush_number): r for r in probe.flush_schedule}
	for row in (doc.get("custom_sf_flush_schedule") or []):
		p = rows.get(cint(row.flush_number))
		if p:
			row.weeks_from_planting = p.weeks_from_planting
			row.harvest_week_of_year = p.harvest_week_of_year


def validate(doc, method=None):
	derive(doc)
