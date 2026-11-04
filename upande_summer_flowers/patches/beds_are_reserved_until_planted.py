# Copyright (c) 2026, James Kiruga and contributors
"""Stop beds claiming a crop that was never planted."""

import frappe


def execute():
	rows = frappe.db.sql("""
		select r.name, r.bed, r.bed_status, r.actual_uproot_date,
		       p.name as planting, p.actual_planting_date
		from `tabPlanting Calendar Bed` r
		join `tabPlanting Calendar` p on p.name = r.parent
		where ifnull(r.actual_uproot_date, '') = ''
		  and ifnull(p.actual_planting_date, '') = ''
		  and r.bed_status = 'Planted'
	""", as_dict=True)
	beds = 0
	for r in rows:
		frappe.db.set_value("Planting Calendar Bed", r.name, "bed_status",
		                    "Reserved", update_modified=False)
		if r.bed and frappe.db.exists("Bed", r.bed):
			# Empty, not Reserved: Bed has its own vocabulary and a bed spoken for
			frappe.db.set_value("Bed", r.bed, "custom_bed_status", "Empty",
			                    update_modified=False)
			beds += 1
	print("  bed rows moved from Planted to Reserved: %d (%d Bed records emptied)"
	      % (len(rows), beds))
