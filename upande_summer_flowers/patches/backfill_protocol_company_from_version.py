# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Stop an approved protocol un-approving itself on every save.

custom_sf_company was empty on protocols whose approved snapshot carried a
company, so diff_against_current reported a change every time and set_status put
the protocol back to Draft -- on a save that altered nothing. Eleven approved
summer flower protocols were in that state, and the only reason it was not
noticed is that nobody had re-saved them since the versions were written.

The value is taken from the protocol's own current version, which is the record
of what was approved. Nothing is invented: where there is no version, or the
version has no company either, the protocol is left alone.
"""

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
