"""bed_area was never computed; derive it from each bed's own dimensions."""

import frappe

from upande_summer_flowers.summer_flowers.bed import backfill_bed_area


def execute():
	if not frappe.db.has_column("Bed", "bed_area"):
		return
	r = backfill_bed_area()
	frappe.logger().info("backfill_bed_area: %s" % r)
