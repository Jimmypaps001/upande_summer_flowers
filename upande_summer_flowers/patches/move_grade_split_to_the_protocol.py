# Copyright (c) 2026, James Kiruga and contributors
"""Take the grade split off the demand register; the protocol is where it lives."""

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
