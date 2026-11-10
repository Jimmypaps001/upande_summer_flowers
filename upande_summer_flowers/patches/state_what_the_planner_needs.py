# Copyright (c) 2026, James Kiruga and contributors
"""Fill the planning figures a protocol must state, before they are demanded.

Crop Protocol Version now refuses to go Active without the figures the planner
divides by and counts forward from. Forty-odd versions on this site are already
Active without them -- they were approved when a blank was allowed, and a blank
read as zero: every planting one bed, no land figure, nothing to order. Demand
them without filling them in and the farm cannot re-approve a protocol at all,
so they are filled here from what the record already implies, and anything that
cannot be worked out is left alone and named in the log."""

import frappe
from frappe.utils import cint, flt

# The farm's beds. 371 of the protocols on this site already say 50.0 m2 net and
# no protocol says anything else, so a protocol that says nothing means this.
STANDARD_BED_SQM = 50.0


def _bed_sqm(version):
	"""What a bed is on this farm, read off the protocols that do say."""
	same = frappe.db.sql("""
		select sqm_net_per_bed, count(*) n from `tabCrop Protocol Version`
		where farm = %s and ifnull(sqm_net_per_bed, 0) > 0
		group by sqm_net_per_bed order by n desc limit 1
	""", version.farm)
	return flt(same[0][0]) if same else STANDARD_BED_SQM


def _growing_cycle(doc):
	"""Which shape of crop this is, from the yield figures already recorded."""
	if doc.get("flush_schedule"):
		return "Perennial - distinct flushes"
	if flt(doc.get("stems_per_plant_per_week")) and cint(doc.get("productive_weeks")):
		return "Perennial - continuous"
	if flt(doc.get("total_stems_per_plant_life")):
		return "Single flush cycle"
	return None


def execute():
	frappe.flags.in_patch = True
	names = frappe.get_all(
		"Crop Protocol Version",
		filters={"version_status": ["in", ("Active", "Draft", "Pending Approval")]},
		pluck="name")
	fixed, stuck = 0, []
	for name in names:
		doc = frappe.get_doc("Crop Protocol Version", name)
		before = (flt(doc.sqm_net_per_bed), flt(doc.min_planting_area_sqm),
		          doc.growing_cycle)

		if not flt(doc.sqm_net_per_bed):
			doc.sqm_net_per_bed = _bed_sqm(doc)
		# The smallest planting worth putting in is one bed, where nothing else
		# has been said. Saying nothing made it zero, which made the minimum
		# meaningless and every planting its own size.
		if not flt(doc.min_planting_area_sqm):
			doc.min_planting_area_sqm = flt(doc.sqm_net_per_bed)
		if not doc.growing_cycle:
			doc.growing_cycle = _growing_cycle(doc)

		after = (flt(doc.sqm_net_per_bed), flt(doc.min_planting_area_sqm),
		         doc.growing_cycle)
		if before == after:
			continue
		# An amendment must say why it was amended. This is why.
		if doc.meta.has_field("change_reason") and not doc.get("change_reason"):
			doc.change_reason = (
				"Planning figures the protocol had left blank, filled in so the "
				"planner reads a stated number rather than a zero.")
		try:
			doc.flags.ignore_protocol_lock = True
			doc.save(ignore_permissions=True)
			fixed += 1
		except Exception as e:
			stuck.append("{0}: {1}".format(name, str(e)[:120]))

	frappe.db.commit()
	print("Crop Protocol Version: {0} filled in".format(fixed))
	if stuck:
		print("  still short, and they will be refused on approval:")
		for line in stuck[:25]:
			print("   ", line)
