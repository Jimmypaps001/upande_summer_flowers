# Copyright (c) 2026, James Kiruga and contributors
"""Summer flower behaviour for Block."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, nowdate
from upande_summer_flowers.summer_flowers import settings as _settings



def _has_role(roles=None):
	# Who counts is set in Summer Flower Settings, not here.
	roles = _settings.approver_roles() if roles is None else roles
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


from upande_summer_flowers.summer_flowers.bed import (
	MAX_CREDIBLE_BED_SQM,
	MIN_CREDIBLE_BED_SQM,
	area_verdict,
	is_credible,
)


def validate_block(doc, method=None):
	"""Only summer flower blocks are touched; every other block is left alone."""
	if not doc.get("custom_is_summer_flower_block"):
		return
	check_area_history(doc)
	apply_current_area(doc)
	check_planted_within_area(doc)
	# Before the dimensions, not after: measure_beds is what fills the bed table
	measure_beds(doc)
	set_dimensions(doc)
	apply_current_coverage(doc)


def set_dimensions(doc):
	"""Work the block's area out from its dimensions, and say what it can hold."""
	gross = flt(doc.get("block_length")) * flt(doc.get("block_width"))
	if not gross:
		gross = flt(doc.get("block_area"))
	doc.custom_sf_gross_area_sqm = gross
	if gross and not flt(doc.get("block_area")):
		doc.block_area = gross

	# The beds are measured where they can be; where they cannot, the block's own
	bed_sqm = sum(flt(r.get("measured_area_sqm")) for r in (doc.get("custom_beds") or []))
	if not bed_sqm:
		bed_sqm = flt(doc.get("custom_net_area_ha")) * 10_000
	doc.custom_sf_plantable_sqm = bed_sqm

	# How many plants it holds is not a property of the block: it depends on how
	if bed_sqm:
		density = 0
		variety = doc.get("variety")
		if variety:
			density = flt(frappe.db.get_value(
				"Crop Protocol Version",
				{"variety": variety, "farm": doc.get("farm"), "is_current": 1},
				"plants_per_sqm_net"))
		if density:
			doc.custom_sf_capacity_note = _(
				"{0} m² of beds inside {1} m² of ground. At {2} plants per m², the "
				"density {3} is planted at, that is about {4} plants."
			).format(int(bed_sqm), int(gross) or _("unmeasured"), density, variety,
			         int(bed_sqm * density))
		else:
			doc.custom_sf_capacity_note = _(
				"{0} m² of beds inside {1} m² of ground. How many plants that holds "
				"depends on the crop's planting density, which its protocol states."
			).format(int(bed_sqm), int(gross) or _("unmeasured"))
	else:
		doc.custom_sf_capacity_note = _(
			"No bed area recorded, so this block cannot say how many plants it holds.")

	# Capacity is what the dimensions are FOR, so that is what is insisted on. A
	if not flt(doc.get("custom_sf_plantable_sqm")):
		frappe.throw(_(
			"{0} has no plantable area. Give it a length and width, or a net area, "
			"or measure its beds -- without one of those it cannot say how many "
			"plants it holds, and no plan can be placed on it."
		).format(doc.name or _("This block")), title=_("Block needs measuring"))


def check_area_history(doc):
	"""Revisions must be dated, ordered, and signed off."""
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
	"""The Bed records that belong to a block. Only those."""
	fields = ["name", "bed", "bed_length", "bed_width", "bed_area", "variety",
	          "custom_plants", "custom_bed_status", "greenhouse"]
	# custom_active belongs to Bed, which this app does not own, and an older
	if frappe.get_meta("Bed").has_field("custom_active"):
		fields.append("custom_active")
	return frappe.get_all("Bed", filters={"custom_block": block},
	                      fields=fields, order_by="bed asc")


def measured_sqm(row):
	"""What a bed measures: its dimensions where it has them, its area otherwise."""
	sqm = flt(row.get("bed_length")) * flt(row.get("bed_width"))
	return sqm or flt(row.get("bed_area"))


def measure_beds(doc):
	"""Fill the bed table and the measured totals from the Bed records."""
	rows = bed_rows(doc.name)
	doc.set("custom_beds", [])
	measured = ok_count = in_service = 0.0
	credible = 0
	for r in rows:
		sqm = measured_sqm(r)
		ok = is_credible(sqm)
		measured += sqm
		if ok:
			credible += 1
			ok_count += sqm
		# Where the field is absent a bed counts as in service: it is what the field
		active = r.get("custom_active", 1) if "custom_active" in r else 1
		if active:
			in_service += 1
		doc.append("custom_beds", {
			"bed": r.name,
			"bed_no": r.get("bed"),
			"in_service": 1 if active else 0,
			"bed_status": r.get("custom_bed_status"),
			"bed_length": r.get("bed_length"),
			"bed_width": r.get("bed_width"),
			"measured_area_sqm": sqm,
			"variety": r.get("variety"),
			"plants": r.get("custom_plants"),
			"area_verdict": area_verdict(sqm),
		})

	doc.custom_measured_beds = len(rows)
	doc.custom_beds_in_service = int(in_service)
	doc.custom_beds_measured_ok = credible
	doc.custom_measured_net_area_ha = measured / 10_000
	stated = flt(doc.get("custom_net_area_ha"))
	doc.custom_area_disagreement_pct = (
		doc.custom_measured_net_area_ha / stated * 100 if stated else 0)

	notes = []
	if not rows:
		notes.append(_("No Bed records point at this block. Set custom_block on its "
		               "beds, or the measured area stays zero and only the stated "
		               "area is available to plan with."))
	blank = len([r for r in rows if not measured_sqm(r)])
	if blank:
		notes.append(_("{0} of {1} beds have neither dimensions nor a recorded area, "
		               "so the measured area understates this block by whatever they "
		               "are. Nothing can compute it for them.").format(blank, len(rows)))
	if credible < len(rows) - blank:
		notes.append(_("{0} beds measure outside {1}-{2} sqm, which is not a bed."
		               ).format(len(rows) - blank - credible,
		                        MIN_CREDIBLE_BED_SQM, MAX_CREDIBLE_BED_SQM))
	if stated and rows and abs(doc.custom_area_disagreement_pct - 100) > 20:
		notes.append(_("Measured area is {0}% of the stated {1} ha. One of the two is "
		               "wrong and the plan will believe the stated figure.").format(
			round(doc.custom_area_disagreement_pct, 1), stated))
	doc.custom_bed_sync_note = "\n".join(notes) or None


@frappe.whitelist()
def link_beds_to_block(block, from_bed=None, to_bed=None):
	"""Point a run of beds at this block."""
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


def apply_current_coverage(doc):
	"""Recount what is standing on this block as part of saving it."""
	if doc.is_new():
		return
	from upande_summer_flowers.summer_flowers.doctype.planting_calendar.planting_calendar import (
		coverage_of,
	)

	for field, value in coverage_of(doc.name, doc.get("custom_total_beds")).items():
		doc.set(field, value)


# ---------------------------------------------------------------------------


def farm_of_greenhouse(greenhouse):
	"""Which farm a house is on, asked of everything that might know."""
	for dt, key in (("Greenhouse", "greenhouse"), ("Block", "greenhouse")):
		if not frappe.db.exists("DocType", dt):
			continue
		farm = frappe.db.get_value(dt, {key: greenhouse, "farm": ["!=", ""]}, "farm")
		if farm:
			return farm
	return None


@frappe.whitelist()
def beds_for_range(greenhouse, first=None, last=None):
	"""The beds in a greenhouse between two numbers, and who already holds them."""
	fields = ["name", "bed", "bed_length", "bed_width", "bed_area", "custom_block",
	          "farm", "greenhouse"]
	filters = {"greenhouse": greenhouse}
	if cint(first):
		filters["bed"] = [">=", cint(first)]
	rows = frappe.get_all("Bed", filters=filters, fields=fields, order_by="bed asc")
	if cint(last):
		rows = [r for r in rows if cint(r.bed) <= cint(last)]

	free = [r for r in rows if not r.custom_block]
	taken = [r for r in rows if r.custom_block]
	measured = sum(measured_sqm(r) for r in free)
	# What a bed measures is not always what it claims; the block will say so on
	unmeasured = sum(1 for r in free if not is_credible(measured_sqm(r)))
	return {
		"greenhouse": greenhouse,
		"farm": next((r.farm for r in rows if r.farm), None)
		        or farm_of_greenhouse(greenhouse),
		"beds": len(rows),
		"free": len(free),
		"free_numbers": [cint(r.bed) for r in free],
		"measured_sqm": round(measured, 1),
		"measured_ha": round(measured / 10_000, 4),
		"unmeasured": unmeasured,
		"taken": [{"bed": cint(r.bed), "block": r.custom_block} for r in taken],
		"first_free": min([cint(r.bed) for r in free], default=None),
		"last_free": max([cint(r.bed) for r in free], default=None),
	}


@frappe.whitelist()
def create_from_beds(greenhouse, block, first=None, last=None, farm=None,
                     summer_flowers=1):
	"""Draw a block over a run of beds in one greenhouse."""
	found = beds_for_range(greenhouse, first, last)
	if not found["beds"]:
		frappe.throw(
			_("No beds in {0}{1}. A block is drawn over beds that already exist, so "
			  "load the beds first.").format(
				greenhouse,
				_(" numbered {0} to {1}").format(first, last) if cint(first) else ""),
			title=_("No beds to draw over"))
	if found["taken"]:
		held = ", ".join("%s (%s)" % (t["bed"], t["block"]) for t in found["taken"][:6])
		frappe.throw(
			_("{0} of these beds already belong to another block: {1}{2}. A bed "
			  "belongs to one block, or two blocks report the same ground as theirs."
			  ).format(len(found["taken"]), held,
			           "…" if len(found["taken"]) > 6 else ""),
			title=_("Beds already spoken for"))

	if not found["measured_sqm"]:
		frappe.throw(
			_("None of these {0} beds has a length and width, so the block would have "
			  "no plantable area and nothing could be planned on it. Measure the beds "
			  "first -- the block takes its area from them.").format(found["free"]),
			title=_("Beds are not measured"))

	farm = farm or found["farm"]
	if not farm:
		frappe.throw(
			_("Nothing says which farm {0} is on -- not its beds, not a Greenhouse "
			  "record for it, and not another block in the same house. Name the farm, "
			  "or record it on the house.").format(greenhouse),
			title=_("No farm"))

	doc = frappe.new_doc("Block")
	doc.greenhouse = greenhouse
	doc.block = block
	doc.farm = farm
	doc.flags.ignore_permissions = True
	# Inserted before the beds are claimed, because a bed points at a block by name
	doc.insert()

	for n in found["free_numbers"]:
		bed = frappe.db.get_value("Bed", {"greenhouse": greenhouse, "bed": n}, "name")
		if bed:
			frappe.db.set_value("Bed", bed, "custom_block", doc.name,
			                    update_modified=False)

	# Now it holds ground, so it can be measured against it: measure_beds fills the
	doc.reload()
	if doc.meta.has_field("custom_is_summer_flower_block"):
		doc.custom_is_summer_flower_block = 1 if cint(summer_flowers) else 0
	doc.flags.ignore_permissions = True
	doc.save()
	return {
		"block": doc.name,
		"beds": found["free"],
		"measured_ha": found["measured_ha"],
		"unmeasured": found["unmeasured"],
		"note": doc.get("custom_sf_capacity_note"),
	}
