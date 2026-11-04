# Copyright (c) 2026, James Kiruga and contributors
"""Drop Product Group from the demand register."""

import frappe

NAME = "Summer Flower Market Demand-product_group"


def execute():
	for ps in frappe.get_all("Property Setter",
	                         filters={"doc_type": "Summer Flower Market Demand",
	                                  "field_name": "product_group"}, pluck="name"):
		frappe.delete_doc("Property Setter", ps, force=True, ignore_permissions=True)

	if frappe.db.exists("Custom Field", NAME):
		frappe.delete_doc("Custom Field", NAME, force=True, ignore_permissions=True)
		print("dropped Summer Flower Market Demand.product_group")

	# delete_doc on a Custom Field does not always take the column with it. DDL has
	frappe.db.commit()
	if frappe.db.has_column("Summer Flower Market Demand", "product_group"):
		frappe.db.sql_ddl("alter table `tabSummer Flower Market Demand` "
		                  "drop column product_group")
		print("dropped the product_group column")

	frappe.db.commit()
	frappe.clear_cache(doctype="Summer Flower Market Demand")
