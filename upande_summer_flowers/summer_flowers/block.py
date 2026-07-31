# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Summer flower behaviour for Block.

A block is a piece of land with a gross area into which a crop can be planted up
to -- but not beyond -- that area. The planting need not fill it. The gross area
legitimately changes over time, so it is held as a dated revision history rather
than a single mutable number, and the current value is derived from that history.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, nowdate

APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")


def _has_role(roles=APPROVER_ROLES):
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


def validate_block(doc, method=None):
	"""Only summer flower blocks are touched; every other block is left alone."""
	if not doc.get("custom_is_summer_flower_block"):
		return
	check_area_history(doc)
	apply_current_area(doc)
	check_planted_within_gross(doc)


def check_area_history(doc):
	"""Revisions must be dated, ordered, and signed off.

	Each revision is what the block measured from that date onwards, so two
	revisions sharing an effective date would make "the area on day X"
	ambiguous.
	"""
	rows = doc.get("custom_area_history") or []
	seen = set()
	for r in rows:
		if not r.effective_from:
			frappe.throw(_("Every area revision needs an effective-from date."))
		if flt(r.gross_area_ha) <= 0:
			frappe.throw(_("Area revision effective {0} has no gross area.")
			             .format(r.effective_from))
		key = str(getdate(r.effective_from))
		if key in seen:
			frappe.throw(_(
				"Two area revisions are effective on {0}. One date can only have "
				"one gross area."
			).format(r.effective_from))
		seen.add(key)
		if not (r.reason or "").strip():
			frappe.throw(_(
				"Area revision effective {0} needs a reason -- a block's area "
				"changing is a physical event and has to be explained."
			).format(r.effective_from))
		if not r.approved_by:
			if not _has_role():
				frappe.throw(_(
					"Changing block area needs Farm Manager approval."))
			r.approved_by = frappe.session.user


def apply_current_area(doc):
	"""Current gross area is the latest revision not in the future."""
	rows = [r for r in (doc.get("custom_area_history") or [])
	        if r.effective_from and getdate(r.effective_from) <= getdate(nowdate())]
	if not rows:
		return
	latest = max(rows, key=lambda r: getdate(r.effective_from))
	doc.custom_gross_area_ha = flt(latest.gross_area_ha)
	if latest.total_beds:
		doc.custom_total_beds = latest.total_beds


def check_planted_within_gross(doc):
	"""Standing plantings cannot claim more beds than the block has."""
	total = int(doc.get("custom_total_beds") or 0)
	if not total:
		return
	from upande_summer_flowers.summer_flowers.doctype.planting_calendar.planting_calendar import (
		STANDING_STATES,
	)
	rows = frappe.get_all(
		"Planting Calendar",
		filters={"block": doc.name, "calendar_status": ["in", STANDING_STATES]},
		fields=["name", "beds"],
	)
	used = sum(int(r.beds or 0) for r in rows)
	if used > total:
		frappe.throw(_(
			"Block {0} has {1} beds but {2} are claimed by standing plantings "
			"({3}). Reduce the plantings before shrinking the block."
		).format(doc.name, total, used, ", ".join(r.name for r in rows)))


@frappe.whitelist()
def revise_area(block, effective_from, gross_area_ha, total_beds=None, reason=None):
	"""Add a dated gross-area revision to a block."""
	if not (reason or "").strip():
		frappe.throw(_("A reason is required to change a block's area."))
	if not _has_role():
		frappe.throw(_("Changing block area needs Farm Manager approval."))
	doc = frappe.get_doc("Block", block)
	doc.append("custom_area_history", {
		"effective_from": getdate(effective_from),
		"gross_area_ha": flt(gross_area_ha),
		"total_beds": int(total_beds) if total_beds else None,
		"reason": reason,
		"approved_by": frappe.session.user,
	})
	doc.save()
	return {"gross_area_ha": doc.custom_gross_area_ha,
	        "total_beds": doc.custom_total_beds}
