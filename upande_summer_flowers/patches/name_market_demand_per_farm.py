# Copyright (c) 2026, James Kiruga and contributors
"""Rename market demand to <variety>-<farm> (94d1d9e)."""

import frappe


def execute():
	if not frappe.db.table_exists("Summer Flower Market Demand"):
		return
	if not frappe.db.has_column("Summer Flower Market Demand", "farm"):
		return

	from frappe.model.rename_doc import rename_doc

	for d in frappe.get_all("Summer Flower Market Demand", fields=["name", "variety", "farm"]):
		if not d.farm:
			# Naming is per farm now and this record does not say which. Left for
			print("Summer Flower Market Demand %s has no farm and cannot be renamed"
			      % d.name)
			continue
		want = "%s-%s" % (d.variety, d.farm)
		if d.name == want:
			continue
		if frappe.db.exists("Summer Flower Market Demand", want):
			print("cannot rename %s -> %s, that name is taken" % (d.name, want))
			continue
		rename_doc("Summer Flower Market Demand", d.name, want, force=True,
		           merge=False, ignore_permissions=True, show_alert=False,
		           rebuild_search=False)
		print("renamed %s -> %s" % (d.name, want))
