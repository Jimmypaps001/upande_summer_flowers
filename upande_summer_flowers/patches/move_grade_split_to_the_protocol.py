# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Take the grade split off the demand register; the protocol is where it lives.

How a plant's stems divide by length is a property of the crop, not of what the
market asked for, and every reader already went to the protocol for it -- the
planning sheet, the crop cycle and the dashboard all read the version's
grade_allocation. The copy on the register was written by the sheet importer and
read by nothing, which is the worst kind of duplicate: it can disagree with the
protocol for years and nobody finds out, because the wrong one is never used.

The walkthrough had a step pointing at the field. A Form Tour reads each step's
field to fill in a label, so a step left behind makes every later migrate fail
with an error that names the framework and not the field.
"""

import frappe

FIELD = "Summer Flower Market Demand-grade_allocation"
TOUR = "Market Demand Walkthrough"


def execute():
	for ps in frappe.get_all("Property Setter",
	                         filters={"doc_type": "Summer Flower Market Demand",
	                                  "field_name": "grade_allocation"}, pluck="name"):
		frappe.delete_doc("Property Setter", ps, force=True, ignore_permissions=True)

	if frappe.db.exists("Custom Field", FIELD):
		frappe.delete_doc("Custom Field", FIELD, force=True, ignore_permissions=True)
		print("dropped the demand register's grade split")

	if frappe.db.exists("Form Tour", TOUR):
		tour = frappe.get_doc("Form Tour", TOUR)
		meta = frappe.get_meta(tour.reference_doctype)
		keep = [s for s in tour.steps if meta.get_field(s.fieldname)]
		if len(keep) != len(tour.steps):
			tour.steps = keep
			for i, step in enumerate(tour.steps, start=1):
				step.idx = i
				if step.title and step.title[0].isdigit():
					step.title = "%d." % i + step.title.split(".", 1)[1]
			tour.flags.ignore_permissions = True
			tour.save()
			print("walkthrough rebuilt with %d steps" % len(tour.steps))

	frappe.db.commit()
	frappe.clear_cache(doctype="Summer Flower Market Demand")
