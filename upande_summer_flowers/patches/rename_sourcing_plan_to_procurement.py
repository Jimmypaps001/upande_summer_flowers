# Copyright (c) 2026, James Kiruga and contributors
"""Summer Flower Sourcing Plan becomes Summer Flower Procurement Plan."""

import frappe

OLD = "Summer Flower Sourcing Plan"
NEW = "Summer Flower Procurement Plan"


def execute():
	if not frappe.db.exists("DocType", OLD):
		return
	if frappe.db.exists("DocType", NEW):
		# The migrate that runs this has already created the new doctype from the
		frappe.delete_doc("DocType", OLD, force=True, ignore_permissions=True)
		frappe.db.commit()
		print("dropped %s; %s ships in its place" % (OLD, NEW))
		return
	frappe.rename_doc("DocType", OLD, NEW, force=True, ignore_permissions=True)
	frappe.db.commit()
	print("renamed %s -> %s" % (OLD, NEW))
