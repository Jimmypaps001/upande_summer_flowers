# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Why the Summer Flowers workspace is not in the sidebar, and how to fix it.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.workspace_doctor.report
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.workspace_doctor.report \\
		--kwargs "{'user': 'someone@example.com'}"

A workspace can be perfectly deployed and still be invisible, with nothing said
anywhere about why. Two rules in frappe/boot.py do it:

1. A sidebar is dropped entirely when no item in it survives the per-item
   permission filter -- `if not any(item["type"] != "Section Break" ...)`. Every
   Section Break is ignored for that test, so a sidebar of headings and nothing
   the user may read simply is not sent to the browser.

2. is_item_allowed (frappe/desk/desk_views.py) passes a DocType item only when
   the name is in BOTH can_read and restricted_doctypes AND has_permission. A
   Workspace item -- the "Home" row at the top -- passes only when that
   workspace is in allowed_workspaces.

Administrator bypasses all of it and sees the sidebar, which is what makes this
so confusing to diagnose: it works for whoever deployed it and for nobody else.
"""

import frappe
from frappe import _

SIDEBAR = "Summer Flowers"
WORKSPACE = "Summer Flowers Planning"


def report(user=None):
	"""Say what is on this site and, for one user, what they would actually see."""
	user = user or frappe.session.user
	print("site: %s   checking as: %s" % (frappe.local.site, user))

	# Every candidate, however it is named or moduled. filters AND or_filters are
	# combined with AND, so pairing a name match with a module match quietly
	# required BOTH -- and dropped the module-less record this is usually about.
	print("\nWorkspace records")
	rows = frappe.get_all(
		"Workspace", fields=["name", "module", "public", "is_hidden", "sequence_id"],
		order_by="sequence_id")
	for w in rows:
		if "flower" not in (w.name or "").lower() and w.module != "Summer Flowers":
			continue
		print("   %-28r module=%-16r public=%s hidden=%s"
		      % (w.name, w.module, w.public, w.is_hidden))

	print("\nWorkspace Sidebar records")
	for s in frappe.get_all("Workspace Sidebar",
	                        fields=["name", "title", "module", "app"]):
		if "flower" not in (s.title or "").lower() and s.module != "Summer Flowers":
			continue
		n = frappe.db.count("Workspace Sidebar Item", {"parent": s.name})
		print("   %-28r module=%-16r app=%-24r items=%d"
		      % (s.title, s.module, s.app, n))

	if not frappe.db.exists("Workspace Sidebar", SIDEBAR):
		print("\n*** No %r sidebar on this site. Nothing will appear." % SIDEBAR)
		return
	_what_user_sees(user)


def _what_user_sees(user):
	"""Run boot.py's own two rules against one user, and name what fails."""
	from frappe.desk.desk_views import DeskViews

	doc = frappe.get_doc("Workspace Sidebar", SIDEBAR)
	views = DeskViews(user=user) if _takes_user() else DeskViews()
	allowed = _allowed_workspaces(user)

	print("\nPer-item filter for %s" % user)
	kept = 0
	for item in doc.items:
		if item.type == "Section Break":
			print("   %-26s (heading, never counts)" % item.label)
			continue
		try:
			ok = views.is_item_allowed(item.link_to, item.link_type, allowed)
		except Exception as e:
			ok = "error: %s" % e
		if ok is True:
			kept += 1
		print("   %-26s %-12s %-34r %s"
		      % (item.label, item.link_type, item.link_to,
		         "shown" if ok is True else "HIDDEN (%s)" % ok))

	print("\n%d item(s) survive." % kept)
	if not kept:
		print("*** boot.py drops the whole sidebar when nothing but headings "
		      "survives, so this user sees no Summer Flowers entry at all and no "
		      "error. Give them read on the doctypes above, or add a role to the "
		      "workspace they already have.")


def _takes_user():
	import inspect
	from frappe.desk.desk_views import DeskViews
	return "user" in inspect.signature(DeskViews.__init__).parameters


def _allowed_workspaces(user):
	try:
		from frappe.desk.desk_views import get_allowed_workspaces
		return get_allowed_workspaces(user=user)
	except Exception:
		return frappe.get_all("Workspace", filters={"public": 1}, pluck="name")
