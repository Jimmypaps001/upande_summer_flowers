# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Drop Product Group from the demand register.

The market sheet groups products above variety -- Aster over Double Date Pink --
and the column was carried across so a register could be read back against the
file it came from. Nothing downstream ever read it, and the Item's own item group
already says Aster wherever that is needed, so it was one more field to fill in
that answered a question nobody asked.

Its own patch rather than a line added to an earlier one: a patch that has already
run on a site is never run again, so extending one is a change that silently does
nothing on every site that mattered.
"""

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
	# to go through sql_ddl and after a commit: a bare ALTER inside the patch's
	# transaction raises ImplicitCommitError, and the rollback takes the field
	# deletion above with it -- which is exactly how this patch failed the first
	# time, reporting the field dropped while leaving it on the form.
	frappe.db.commit()
	if frappe.db.has_column("Summer Flower Market Demand", "product_group"):
		frappe.db.sql_ddl("alter table `tabSummer Flower Market Demand` "
		                  "drop column product_group")
		print("dropped the product_group column")

	frappe.db.commit()
	frappe.clear_cache(doctype="Summer Flower Market Demand")
