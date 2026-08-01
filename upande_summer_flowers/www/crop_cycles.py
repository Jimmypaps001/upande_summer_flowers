# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Controller for /crop-cycles.

Frappe pairs a www template with the basename's hyphens converted to
underscores, so this file must be crop_cycles.py.
"""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to view the crop cycle dashboard."),
		             frappe.PermissionError)

	context.no_cache = 1
	context.title = _("Crop Cycles — Planning and Management")
	context.show_sidebar = False
	return context
