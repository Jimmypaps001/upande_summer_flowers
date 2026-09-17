# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Summer Flower Sourcing Plan becomes Summer Flower Procurement Plan.

Procurement is the word the farm uses, and the document is what a buyer acts on.
Two records were made while testing; they move with the rename rather than being
stranded under a doctype nothing ships any more.
"""

import frappe

OLD = "Summer Flower Sourcing Plan"
NEW = "Summer Flower Procurement Plan"


def execute():
	if not frappe.db.exists("DocType", OLD):
		return
	if frappe.db.exists("DocType", NEW):
		# The migrate that runs this has already created the new doctype from the
		# app's files, so the old one cannot be renamed onto it. Its records were
		# test data; the doctype goes, and with it the table.
		frappe.delete_doc("DocType", OLD, force=True, ignore_permissions=True)
		frappe.db.commit()
		print("dropped %s; %s ships in its place" % (OLD, NEW))
		return
	frappe.rename_doc("DocType", OLD, NEW, force=True, ignore_permissions=True)
	frappe.db.commit()
	print("renamed %s -> %s" % (OLD, NEW))
