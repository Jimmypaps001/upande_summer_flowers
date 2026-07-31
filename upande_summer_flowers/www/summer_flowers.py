# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		# A 403 wall is a dead end; send them somewhere they can act.
		frappe.local.flags.redirect_location = "/login?redirect-to=/summer-flowers"
		raise frappe.Redirect

	context.no_cache = 1
	# Without this the browser keeps serving yesterday's JavaScript, and a fix
	# looks like it never landed.
	if getattr(frappe.local, "response_headers", None) is not None:
		frappe.local.response_headers["Cache-Control"] = "no-store, must-revalidate"
	context.title = _("Summer Flowers")
	context.show_sidebar = False
	return context
