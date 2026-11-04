# Copyright (c) 2026, James Kiruga and contributors
"""Stop an approved protocol un-approving itself on every save."""

import frappe


def execute():
	if not frappe.db.table_exists("Crop Protocol"):
		return
	if not frappe.db.table_exists("Crop Protocol Version"):
		return
	for col in ("custom_sf_company", "custom_sf_current_version"):
		if not frappe.db.has_column("Crop Protocol", col):
			return
	if not frappe.db.has_column("Crop Protocol Version", "company"):
		return

	rows = frappe.db.sql("""select p.name, v.company
	                        from `tabCrop Protocol` p
	                        join `tabCrop Protocol Version` v
	                          on v.name = p.custom_sf_current_version
	                        where ifnull(p.custom_sf_company, '') = ''
	                          and ifnull(v.company, '') != ''""", as_dict=True)
	for r in rows:
		frappe.db.set_value("Crop Protocol", r.name, "custom_sf_company", r.company,
		                    update_modified=False)
	if rows:
		print("company taken from its own approved snapshot on %d protocol(s)" % len(rows))
