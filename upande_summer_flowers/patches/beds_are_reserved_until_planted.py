# Copyright (c) 2026, James Kiruga and contributors
"""Stop beds claiming a crop that was never planted.

allocate_beds() gave every newly allocated bed the status "Planted", because the
allocation table had no word for a bed that is merely spoken for. So allocating a
block marked its beds as carrying a standing crop -- on this bench, 858 bed rows
and 970 Bed records said Planted while not one planting had an actual planting
date against it.

Reserved now means allocated, Planted means somebody recorded the day it went in.
This corrects what is already written: a bed belonging to a planting that has not
been planted goes back to Reserved, and its Bed record back to Empty. Beds that
were uprooted, or that belong to a planting with a real date on it, are left
exactly as they are.
"""

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
			# by a future planting is empty ground today.
			frappe.db.set_value("Bed", r.bed, "custom_bed_status", "Empty",
			                    update_modified=False)
			beds += 1
	print("  bed rows moved from Planted to Reserved: %d (%d Bed records emptied)"
	      % (len(rows), beds))
