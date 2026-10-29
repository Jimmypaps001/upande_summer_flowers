# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Make this app's custom fields exist before a patch uses them.

Frappe syncs an app's `custom/*.json` AFTER its post-model-sync patches run, so
a patch that writes to a custom field finds nothing there on a site that has not
had that field before -- and dies with AttributeError mid-migrate. It passes on
a bench where the field already exists, which is every bench it was written on.
"""

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
		# synced, will do the work -- which is better than taking the whole
		# migrate down over a field that is one step away.
		print("Skipping: %s has no %s yet" % (doctype, ", ".join(missing)))
		return False
	return True
