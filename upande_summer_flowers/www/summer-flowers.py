# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to view the Summer Flowers dashboard."),
		             frappe.PermissionError)

	context.no_cache = 1
	context.title = _("Summer Flowers")
	context.show_sidebar = False
	return context
