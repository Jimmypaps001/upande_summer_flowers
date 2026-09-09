# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import frappe
from frappe import _

# Route deliberately NOT /summer-flowers. A Workspace's route is its slugified
# name, so the Summer Flowers workspace lives at /desk/summer-flowers -- and a
# portal page at /summer-flowers shadowed it. Anything that reached the bare
# route got this dashboard instead of the workspace, and because the page is
# titled "Summer Flowers" and carries the same logo it read as the workspace
# having turned into a dashboard.
no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to view the Summer Flowers dashboard."),
		             frappe.PermissionError)

	context.no_cache = 1
	context.title = _("Summer Flowers — Overview")
	context.show_sidebar = False
	return context
