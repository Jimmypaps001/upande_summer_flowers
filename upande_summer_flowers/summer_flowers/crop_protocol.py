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

# Natives the protocol is EDITED in. Read from, never written back to: they are the
# inputs. Writing to them is what wiped a brand-new protocol's variety, because the
# version derives its own variety from its parent protocol and on a first insert that
# parent does not exist yet, so the write-back put None over what had just been typed.
INPUT_NATIVE = {"farm", "variety", "plants_per_sqm", "weeks_to_pinch",
                "weeks_between_flushes", "yield_stems_per_ha"}
# Natives the protocol DERIVES into. These sat at zero for summer flowers before.
DERIVED_NATIVE = {"total_weeks_in_ground", "total_flushes",
                  "total_stems_per_plant_life", "life_expectancy_years"}

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
		target = source_field(f.fieldname)
		# Only our own fields and the four natives this protocol is meant to derive.
		# Anything else on Crop Protocol belongs to whoever typed it.
		if target in INPUT_NATIVE or (
			not target.startswith("custom_sf_") and target not in DERIVED_NATIVE
		):
			continue
		doc.set(target, probe.get(f.fieldname))
	# The flush rows gain their derived columns too.
	rows = {cint(r.flush_number): r for r in probe.flush_schedule}
	for row in (doc.get("custom_sf_flush_schedule") or []):
		p = rows.get(cint(row.flush_number))
		if p:
			row.weeks_from_planting = p.weeks_from_planting
			row.harvest_week_of_year = p.harvest_week_of_year


GROWTH_STAGES = [
	# label,                  from week expression,        to week expression
	("Sticking to rooting", "0", "weeks_on_tray"),
	("Rooted, on pot", "weeks_on_tray", "weeks_on_tray + weeks_on_pot"),
	("Hardening", "weeks_on_tray + weeks_on_pot",
	 "weeks_on_tray + weeks_on_pot + hardening_weeks"),
	("Planted to pinch", "0", "weeks_to_pinch"),
	("Pinch to first harvest", "weeks_to_pinch", "first_harvest"),
	("Flushing", "first_harvest", "life"),
]


def set_growth_stages(doc):
	"""Fill Crop Protocol's own growth stage table from the protocol's timeline.

	Derived, not typed in: every boundary is a figure already on the protocol, so
	the stages cannot drift from the weeks that drive the planner. The propagation
	stages are measured from sticking, the field stages from planting -- they are
	two different clocks and labelling them as one would misread both.
	"""
	if not is_summer_flower(doc):
		return
	g = lambda f: cint(doc.get("custom_sf_" + f))
	env = {
		"weeks_on_tray": g("weeks_on_tray"),
		"weeks_on_pot": g("weeks_on_pot"),
		"hardening_weeks": g("hardening_weeks"),
		"weeks_to_pinch": cint(doc.get("weeks_to_pinch")),
		"first_harvest": cint(doc.get("custom_sf_first_harvest_offset_weeks")),
		"life": cint(doc.get("total_weeks_in_ground")),
	}
	if not any(env.values()):
		return
	rows = []
	for order, (label, frm, to) in enumerate(GROWTH_STAGES, start=1):
		try:
			a, b = int(eval(frm, {}, env)), int(eval(to, {}, env))
		except Exception:
			continue
		if b <= a:
			continue
		rows.append({
			"stage_name": label,
			"stage_order": order,
			"days_from": a * 7,
			"days_to": b * 7,
			"weeks": b - a,
			"description": _("Weeks {0} to {1} {2}").format(
				a, b, _("from sticking") if order <= 3 else _("from planting")),
		})
	if not rows:
		return
	doc.set("growth_stages", [])
	for r in rows:
		doc.append("growth_stages", r)


def set_length_distribution(doc):
	"""Deliberately does nothing: the native table is not the same thing.

	Crop Protocol Length Distribution.stem_length is a Link to the Stem Length
	master, whose records are 57, 62, 72, 82 and 92cm -- measured sampling bands
	against a controlled list. Our grade split is 50/60/70/80cm with a price per
	stem, a commercial allocation. Mirroring one into the other either invents
	records in another app's master or writes dangling links, which is what it did
	before this was reverted.

	The grade split therefore stays in the summer flower grade table, which is the
	one and only place it is authored and the one every consumer reads.
	"""
	return


def set_native_flush_schedule(doc):
	"""Mirror the flush schedule into Crop Protocol's own table.

	The two child doctypes are not interchangeable -- ours carries weeks from pinch,
	weeks from planting and the harvest week of the year, Crop Protocol Flush carries
	the gap from the previous flush -- so the summer flower schedule is authored in
	ours. Without this mirror the protocol showed an EMPTY flush schedule in its
	standard layout while the real eight rows sat in the custom table below, which is
	exactly the sort of two-places-one-fact this restructure is meant to remove.
	"""
	if not is_summer_flower(doc):
		return
	rows = sorted((doc.get("custom_sf_flush_schedule") or []),
	              key=lambda r: cint(r.flush_number))
	if not rows:
		return
	doc.set("flush_schedule", [])
	prev = 0
	for r in rows:
		frm_pinch = cint(r.weeks_from_pinch)
		doc.append("flush_schedule", {
			"flush_number": cint(r.flush_number),
			"stems_per_plant": flt(r.stems_per_plant),
			# Crop Protocol Flush measures the gap from the flush before it; the first
			# gap is from the pinch, which is where our schedule starts counting.
			"weeks_after_previous": frm_pinch - prev,
		})
		prev = frm_pinch


def fill_native_gaps(doc):
	"""Give the natives our own numbers instead of leaving them at zero.

	These are Crop Protocol's own fields that a rose protocol fills in by hand. For
	a summer flower they are all derivable, and leaving them at zero beside our
	populated ones is the repetition that makes the form unreadable.
	"""
	if not is_summer_flower(doc):
		return
	rows = sorted((doc.get("custom_sf_flush_schedule") or []),
	              key=lambda r: cint(r.flush_number))
	if rows:
		# The native field asks for pinch to first harvest, which is our first
		# flush's own offset from the pinch.
		doc.weeks_pinch_to_first_harvest = cint(rows[0].weeks_from_pinch)
	grades = doc.get("custom_sf_grade_allocation") or []
	total = sum(flt(g.allocation_pct) for g in grades)
	if grades and total:
		# Weighted mean of the grade bands: 60cm at 30%, 70 at 40%, 80 at 30% is 71.
		acc = 0.0
		for g in grades:
			digits = "".join(ch for ch in (g.grade or "") if ch.isdigit())
			if digits:
				acc += float(digits) * flt(g.allocation_pct)
		doc.average_stem_length_cm = round(acc / total, 2) if acc else 0
	# Weeks to max PC and the ramp are the same span; the ramp is the one typed.
	if cint(doc.get("custom_sf_ramp_weeks")):
		doc.custom_sf_weeks_to_max_pc = cint(doc.get("custom_sf_ramp_weeks"))


def validate(doc, method=None):
	# The ramp drives weeks-to-max-PC, and the establishment total is built from
	# that, so it is squared away before anything is derived.
	if is_summer_flower(doc) and cint(doc.get("custom_sf_ramp_weeks")):
		doc.custom_sf_weeks_to_max_pc = cint(doc.get("custom_sf_ramp_weeks"))
	derive(doc)
	fill_native_gaps(doc)
	set_growth_stages(doc)
	set_length_distribution(doc)
	set_native_flush_schedule(doc)
