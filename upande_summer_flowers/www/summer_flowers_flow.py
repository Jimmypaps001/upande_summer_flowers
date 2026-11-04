# Copyright (c) 2026, James Kiruga and contributors
"""Controller for /summer-flowers-flow."""

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
