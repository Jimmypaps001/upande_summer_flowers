# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Controller for /summer-flowers-planning.

Frappe pairs a www template with the basename's hyphens converted to
underscores, so this file must be summer_flowers_planning.py.
"""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to view the planning dashboard."),
		             frappe.PermissionError)

	context.no_cache = 1
	context.title = _("Summer Flowers — Planning")
	context.show_sidebar = False
	return context
