# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Controller for /summer-flowers-flow.

Frappe pairs a www template with the basename's hyphens converted to
underscores, so this file must be summer_flowers_flow.py.

The page is a guide, not a dashboard: every figure on it is a worked example
written into the template, so it renders without reading the database and cannot
go blank because a site has no data yet.
"""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to read the planning guide."),
		             frappe.PermissionError)

	context.no_cache = 1
	context.title = _("Summer Flowers — How Planning Works")
	context.show_sidebar = False
	return context
