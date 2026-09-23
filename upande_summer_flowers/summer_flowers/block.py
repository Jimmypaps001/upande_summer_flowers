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
from frappe.utils import cint, flt, getdate, now_datetime, nowdate

APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")


def _has_role(roles=APPROVER_ROLES):
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
	# from the Bed records, and the plantable area is read off that table. Running
	# the check first refused a block whose beds are measured but whose table had
	# not been built yet -- which is every block on a site that has not saved one
	# since the beds were loaded.
	measure_beds(doc)
	set_dimensions(doc)
	apply_current_coverage(doc)


def set_dimensions(doc):
	"""Work the block's area out from its dimensions, and say what it can hold.

	A block without dimensions cannot say how many plants it takes, and that is
	the question every plan asks of it. Length by width is the gross ground; the
	beds inside it are what a crop is actually planted on, and the difference is
	paths and headlands, which grow nothing.
	"""
	gross = flt(doc.get("block_length")) * flt(doc.get("block_width"))
	if not gross:
		gross = flt(doc.get("block_area"))
	doc.custom_sf_gross_area_sqm = gross
	if gross and not flt(doc.get("block_area")):
		doc.block_area = gross

	# The beds are measured where they can be; where they cannot, the block's own
	# net area stands in, because a bed with no dimensions is not no bed.
	bed_sqm = sum(flt(r.get("measured_area_sqm")) for r in (doc.get("custom_beds") or []))
	if not bed_sqm:
		bed_sqm = flt(doc.get("custom_net_area_ha")) * 10_000
	doc.custom_sf_plantable_sqm = bed_sqm

	# How many plants it holds is not a property of the block: it depends on how
	# densely the crop is planted, which the protocol states. So the note gives the
	# ground and works an example at the density of whatever is standing there.
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
	# block measured by length and width has it; so does one whose beds are
	# measured. A block with neither cannot answer the only question a plan asks
	# of it, and saying "give me a width" would be asking for the wrong thing.
	if not flt(doc.get("custom_sf_plantable_sqm")):
		frappe.throw(_(
			"{0} has no plantable area. Give it a length and width, or a net area, "
			"or measure its beds -- without one of those it cannot say how many "
			"plants it holds, and no plan can be placed on it."
		).format(doc.name or _("This block")), title=_("Block needs measuring"))


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
	"""The Bed records that belong to a block. Only those.

	By custom_block, which is the only link a Bed has to a Block. An earlier version
	fell back to the unassigned beds in the block's greenhouse so that a block with
	nothing linked would not read as empty land, and that was wrong twice over: a bed
	belongs to a block or to a greenhouse and not to both, and those same beds were
	counted again under every other block in the house. A block with no beds linked
	reports no measured area, and the note says to link them.
	"""
	fields = ["name", "bed", "bed_length", "bed_width", "bed_area", "variety",
	          "custom_plants", "custom_bed_status", "greenhouse"]
	# custom_active belongs to Bed, which this app does not own, and an older
	# propagation app has not got it. Asked for only where it exists: a column that
	# is not there takes down the SELECT, and this runs on every Block save, so one
	# absent field on another app's doctype made blocks impossible to create at all.
	if frappe.get_meta("Bed").has_field("custom_active"):
		fields.append("custom_active")
	return frappe.get_all("Bed", filters={"custom_block": block},
	                      fields=fields, order_by="bed asc")


def measured_sqm(row):
	"""What a bed measures: its dimensions where it has them, its area otherwise.

	Both cases are real on this site and they arrived in that order. Greenhouse beds
	carry a length and a width and had bed_area sitting at zero, so this computed
	from the dimensions and ignored the stored figure. The KR farm blocks are the
	other way round -- every bed is known to be 50 m² and nobody has measured a
	length or a width -- and computing unconditionally reported 4,789 beds covering
	nought hectares.
	"""
	sqm = flt(row.get("bed_length")) * flt(row.get("bed_width"))
	return sqm or flt(row.get("bed_area"))


def measure_beds(doc):
	"""Fill the bed table and the measured totals from the Bed records.

	Stated beds and stated area come from the area history, which is what the block
	claims. These are what actually exists, kept separately and deliberately not
	overwriting the claim: the two disagree on this site and which one is right is
	a question for a farm manager, not for a planner reading whichever was written
	last.
	"""
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
		# defaults to where it exists, and reading "not in service" off a column that
		# was never there would report every bed on the farm as out of use.
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


def apply_current_coverage(doc):
	"""Recount what is standing on this block as part of saving it.

	Occupied and free beds depend on the block's own bed count, not only on the
	plantings, and they were recomputed only when a planting changed. Reloading
	Karen's blocks from 40 beds to 80 therefore left all 73 reporting the free beds
	they had before -- Block 5A said "occupied 19, free 21" of 80.
	"""
	if doc.is_new():
		return
	from upande_summer_flowers.summer_flowers.doctype.planting_calendar.planting_calendar import (
		coverage_of,
	)

	for field, value in coverage_of(doc.name, doc.get("custom_total_beds")).items():
		doc.set(field, value)


# ---------------------------------------------------------------------------
# Building a block out of beds
# ---------------------------------------------------------------------------
#
# A block is a stretch of a greenhouse: so many beds, end to end, under one roof.
# Nothing in the app made one -- the seventy-one blocks at Kariki were loaded by a
# console script nobody else can run, and a block drawn by hand carries no beds, so
# it reports no area and no capacity and the planner refuses to place anything on
# it. This is that script with the guardrails it always needed.


def farm_of_greenhouse(greenhouse):
	"""Which farm a house is on, asked of everything that might know.

	Most Bed records do not carry a farm -- twenty thousand of the twenty-five
	thousand on this bench -- so reading it off them alone refused to build a block
	over beds that are perfectly well placed. The Greenhouse record knows, and where
	there is none, a block already drawn in the same house does.
	"""
	for dt, key in (("Greenhouse", "greenhouse"), ("Block", "greenhouse")):
		if not frappe.db.exists("DocType", dt):
			continue
		farm = frappe.db.get_value(dt, {key: greenhouse, "farm": ["!=", ""]}, "farm")
		if farm:
			return farm
	return None


@frappe.whitelist()
def beds_for_range(greenhouse, first=None, last=None):
	"""The beds in a greenhouse between two numbers, and who already holds them.

	Read-only, so the dialog can show what it is about to take before it takes it:
	how many beds, how much measured ground, and which of them are spoken for.
	"""
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
	# save, but a range that is mostly unmeasured is worth knowing before creating.
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
	"""Draw a block over a run of beds in one greenhouse.

	The beds are what make it a block: they carry its area, its capacity and, once
	something is planted, its coverage. So they are claimed here -- `custom_block`
	on each Bed -- rather than left for somebody to link one at a time.

	A bed already in another block is refused by name. Silently re-pointing it
	would take ground away from a block that may have a crop standing on it, and
	the two blocks would then both report the same beds as theirs.
	"""
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
	# and the name does not exist until it is saved. And inserted WITHOUT the summer
	# flower flag, because that is what turns on the area check -- which a block
	# would fail on its first save every time, having no beds yet to measure. The
	# chicken and the egg, resolved in the only order that works.
	doc.insert()

	for n in found["free_numbers"]:
		bed = frappe.db.get_value("Bed", {"greenhouse": greenhouse, "bed": n}, "name")
		if bed:
			frappe.db.set_value("Bed", bed, "custom_block", doc.name,
			                    update_modified=False)

	# Now it holds ground, so it can be measured against it: measure_beds fills the
	# bed table and the area, set_dimensions works out the capacity, and the flag is
	# safe to set because there is finally something to check.
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
