# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Summer flower behaviour for Block.

A block is a piece of land with a net bed area into which a crop can be planted
up to -- but not beyond -- that area. The planting need not fill it. That area
legitimately changes over time, so it is held as a dated revision history rather
than a single mutable number, and the current value is derived from that history.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, nowdate

APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")


def _has_role(roles=APPROVER_ROLES):
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


# A bed smaller than this is not a bed, and one larger than this is not a bed
# either -- both are data entry. The measured area of a block full of 2 sqm beds
# would otherwise read as real capacity and the planner would believe it.
MIN_CREDIBLE_BED_SQM = 5.0
MAX_CREDIBLE_BED_SQM = 400.0


def validate_block(doc, method=None):
	"""Only summer flower blocks are touched; every other block is left alone."""
	if not doc.get("custom_is_summer_flower_block"):
		return
	check_area_history(doc)
	apply_current_area(doc)
	check_planted_within_area(doc)
	measure_beds(doc)


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
		if flt(r.net_area_ha) <= 0:
			frappe.throw(_("Area revision effective {0} has no net area.")
			             .format(r.effective_from))
		key = str(getdate(r.effective_from))
		if key in seen:
			frappe.throw(_(
				"Two area revisions are effective on {0}. One date can only have "
				"one net area."
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
	"""Current net area is the latest revision not in the future."""
	rows = [r for r in (doc.get("custom_area_history") or [])
	        if r.effective_from and getdate(r.effective_from) <= getdate(nowdate())]
	if not rows:
		return
	latest = max(rows, key=lambda r: getdate(r.effective_from))
	doc.custom_net_area_ha = flt(latest.net_area_ha)
	if latest.total_beds:
		doc.custom_total_beds = latest.total_beds


def check_planted_within_area(doc):
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
def revise_area(block, effective_from, net_area_ha, total_beds=None, reason=None):
	"""Add a dated net-area revision to a block."""
	if not (reason or "").strip():
		frappe.throw(_("A reason is required to change a block's area."))
	if not _has_role():
		frappe.throw(_("Changing block area needs Farm Manager approval."))
	doc = frappe.get_doc("Block", block)
	doc.append("custom_area_history", {
		"effective_from": getdate(effective_from),
		"net_area_ha": flt(net_area_ha),
		"total_beds": int(total_beds) if total_beds else None,
		"reason": reason,
		"approved_by": frappe.session.user,
	})
	doc.save()
	return {"net_area_ha": doc.custom_net_area_ha,
	        "total_beds": doc.custom_total_beds}


# --------------------------------------------------------------- bed inventory
def bed_rows(block, greenhouse=None):
	"""The Bed records that belong to a block.

	By custom_block, which is the only link a Bed has to a Block. Where nothing is
	linked the greenhouse is a last resort so a block in a house full of beds does
	not report itself as empty land -- but it is reported as a guess, because a
	greenhouse holds many blocks and claiming all of its beds would overstate this
	one enormously.
	"""
	fields = ["name", "bed", "bed_length", "bed_width", "bed_area", "variety",
	          "custom_plants", "custom_bed_status", "custom_active", "greenhouse"]
	rows = frappe.get_all("Bed", filters={"custom_block": block},
	                      fields=fields, order_by="bed asc")
	if rows:
		return rows, False
	if not greenhouse:
		return [], False
	return frappe.get_all("Bed", filters={"greenhouse": greenhouse, "custom_block": ["is", "not set"]},
	                      fields=fields, order_by="bed asc"), True


def measured_sqm(row):
	"""Length x width. Bed.bed_area is not read.

	It is 0.0 on all 20,668 beds on this site -- never computed by whatever writes
	Bed -- so trusting it would make every block zero hectares.
	"""
	return flt(row.get("bed_length")) * flt(row.get("bed_width"))


def measure_beds(doc):
	"""Fill the bed table and the measured totals from the Bed records.

	Stated beds and stated area come from the area history, which is what the block
	claims. These are what actually exists, kept separately and deliberately not
	overwriting the claim: the two disagree on this site and which one is right is
	a question for a farm manager, not for a planner reading whichever was written
	last.
	"""
	rows, guessed = bed_rows(doc.name, doc.get("greenhouse"))
	doc.set("custom_beds", [])
	measured = ok_count = in_service = 0.0
	credible = 0
	for r in rows:
		sqm = measured_sqm(r)
		ok = MIN_CREDIBLE_BED_SQM <= sqm <= MAX_CREDIBLE_BED_SQM
		measured += sqm
		if ok:
			credible += 1
			ok_count += sqm
		if r.get("custom_active"):
			in_service += 1
		doc.append("custom_beds", {
			"bed": r.name,
			"bed_no": r.get("bed"),
			"in_service": 1 if r.get("custom_active") else 0,
			"bed_status": r.get("custom_bed_status"),
			"bed_length": r.get("bed_length"),
			"bed_width": r.get("bed_width"),
			"measured_area_sqm": sqm,
			"variety": r.get("variety"),
			"plants": r.get("custom_plants"),
			"area_verdict": (
				_("ok") if ok else
				_("no dimensions") if not sqm else
				_("{0} sqm -- not credible").format(round(sqm, 1))
			),
		})

	# A guess is shown but never totalled. The unassigned beds in a greenhouse are
	# the same beds for every block in it, so counting them per block added the same
	# 66 beds twice and made a farm's measured area exceed its stated area -- the one
	# thing the measured column exists to contradict.
	doc.custom_measured_beds = 0 if guessed else len(rows)
	doc.custom_beds_in_service = 0 if guessed else int(in_service)
	doc.custom_beds_measured_ok = 0 if guessed else credible
	doc.custom_measured_net_area_ha = 0 if guessed else measured / 10_000
	stated = flt(doc.get("custom_net_area_ha"))
	doc.custom_area_disagreement_pct = (
		doc.custom_measured_net_area_ha / stated * 100 if stated else 0)

	notes = []
	if not rows:
		notes.append(_("No Bed records point at this block. Set custom_block on its "
		               "beds, or the measured area stays zero and only the stated "
		               "area is available to plan with."))
	if guessed:
		notes.append(_("These beds are the unassigned beds in {0}, not beds linked to "
		               "this block. A greenhouse holds several blocks, so treat the "
		               "measured area as an upper bound.").format(doc.greenhouse))
	if credible < len(rows):
		notes.append(_("{0} of {1} beds have dimensions that cannot be a bed (under "
		               "{2} or over {3} sqm), so the measured area understates this "
		               "block.").format(len(rows) - credible, len(rows),
		                                MIN_CREDIBLE_BED_SQM, MAX_CREDIBLE_BED_SQM))
	if stated and rows and abs(doc.custom_area_disagreement_pct - 100) > 20:
		notes.append(_("Measured area is {0}% of the stated {1} ha. One of the two is "
		               "wrong and the plan will believe the stated figure.").format(
			round(doc.custom_area_disagreement_pct, 1), stated))
	doc.custom_bed_sync_note = "\n".join(notes) or None


@frappe.whitelist()
def link_beds_to_block(block, from_bed=None, to_bed=None):
	"""Point a run of beds at this block.

	Only 480 of the beds on this site are linked to any block, which is why blocks
	report no measured area. Beds are numbered within a greenhouse, so a range is
	how a farm manager actually describes which ones belong to a block.
	"""
	doc = frappe.get_doc("Block", block)
	if not _has_role():
		frappe.throw(_("Only a Farm Manager or Agriculture Manager can assign beds "
		               "to a block."))
	filters = {"greenhouse": doc.greenhouse}
	if from_bed:
		filters["bed"] = [">=", int(from_bed)]
	names = [b.name for b in frappe.get_all("Bed", filters=filters, fields=["name", "bed"])
	         if not to_bed or (b.bed or 0) <= int(to_bed)]
	for name in names:
		frappe.db.set_value("Bed", name, "custom_block", block, update_modified=False)
	doc.save()
	return {"linked": len(names), "measured_ha": doc.custom_measured_net_area_ha}
