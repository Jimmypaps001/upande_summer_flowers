# Copyright (c) 2026, James Kiruga and contributors
"""Make this app's custom fields exist before a patch uses them."""

import frappe


def ensure(doctype, *fieldnames):
	"""Sync this app's customisations, then say whether the fields are there."""
	meta = frappe.get_meta(doctype)
	if all(meta.get_field(f) for f in fieldnames):
		return True
	try:
		from frappe.modules.utils import sync_customizations

		sync_customizations("upande_summer_flowers")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sync_customizations in patch")
	frappe.clear_cache(doctype=doctype)
	meta = frappe.get_meta(doctype)
	missing = [f for f in fieldnames if not meta.get_field(f)]
	if missing:
		# Nothing to write into yet. The next migrate, with the customisation
		print("Skipping: %s has no %s yet" % (doctype, ", ".join(missing)))
		return False
	return True
