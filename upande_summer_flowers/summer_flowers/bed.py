# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Summer flower behaviour for Bed.

A bed is the unit of space. Two facts about it were unreliable: its area was
never computed from its own dimensions, and it could appear to belong to a block
and to a greenhouse independently, so the same land could be counted twice.
"""

import frappe
from frappe import _
from frappe.utils import flt

# A bed with no length or width is not a small bed, it is an unmeasured one, and
# the two have to be told apart: the first would shrink a block's capacity, the
# second is a gap in the register.
MIN_CREDIBLE_BED_SQM = 5.0
MAX_CREDIBLE_BED_SQM = 400.0


def validate_bed(doc, method=None):
	set_area(doc)
	check_single_owner(doc)


def set_area(doc):
	"""Area is length x width, always.

	bed_area was 0 on all 20,668 beds on this site while length and width were
	filled in, so every consumer either ignored it or read zero hectares of land.
	It is derived, so it is derived here rather than typed.
	"""
	doc.bed_area = flt(doc.bed_length) * flt(doc.bed_width)


def check_single_owner(doc):
	"""A bed sits under a block or directly under a greenhouse, never both.

	Both links exist on Bed, and nothing stopped them disagreeing -- a bed in block
	11A of one house while its greenhouse field named another. The block is inside a
	greenhouse, so the block is the more specific owner and the greenhouse must be
	the block's own; anything else is two claims on one piece of land.
	"""
	if not doc.get("custom_block"):
		return
	gh = frappe.db.get_value("Block", doc.custom_block, "greenhouse")
	if gh and doc.greenhouse and gh != doc.greenhouse:
		frappe.throw(_(
			"This bed is in block {0}, which is in {1}, but its greenhouse says {2}. "
			"A bed belongs to a block or to a greenhouse, not to both -- one of the "
			"two has to change."
		).format(doc.custom_block, gh, doc.greenhouse), title=_("Two owners"))
	if gh and not doc.greenhouse:
		doc.greenhouse = gh


def area_verdict(sqm):
	"""Why a bed's measured area cannot be used, in the bed's own terms."""
	if not sqm:
		return _("no dimensions recorded")
	if sqm < MIN_CREDIBLE_BED_SQM:
		return _("{0} sqm -- too small to be a bed").format(round(sqm, 1))
	if sqm > MAX_CREDIBLE_BED_SQM:
		return _("{0} sqm -- too large to be a bed").format(round(sqm, 1))
	return _("ok")


def is_credible(sqm):
	return bool(sqm) and MIN_CREDIBLE_BED_SQM <= sqm <= MAX_CREDIBLE_BED_SQM


@frappe.whitelist()
def backfill_bed_area(limit=None):
	"""Compute bed_area from length x width for every bed that has dimensions.

	Set once, in SQL, because there are 20,668 of them and loading each document
	would take minutes and fire every other app's Bed hooks for a derived number.
	Beds with no dimensions are left at zero and counted, because inventing an area
	for them would be worse than the gap.
	"""
	rows = frappe.db.sql("""
		select name, bed_length, bed_width, bed_area from tabBed
	""", as_dict=True)
	fixed = blank = already = 0
	for r in rows:
		sqm = flt(r.bed_length) * flt(r.bed_width)
		if not sqm:
			blank += 1
			continue
		if abs(flt(r.bed_area) - sqm) < 0.0001:
			already += 1
			continue
		frappe.db.set_value("Bed", r.name, "bed_area", sqm, update_modified=False)
		fixed += 1
	frappe.db.commit()
	return {"beds": len(rows), "areas_written": fixed, "already_correct": already,
	        "no_dimensions": blank}
