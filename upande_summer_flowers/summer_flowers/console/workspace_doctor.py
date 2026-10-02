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

import os

import frappe
from frappe import _

APP = "upande_summer_flowers"
SIDEBAR = "Summer Flowers"
WORKSPACE = "Summer Flowers Planning"


def report(user=None):
	"""Say what is on this site and, for one user, what they would actually see."""
	user = user or frappe.session.user
	print("site: %s   checking as: %s" % (frappe.local.site, user))
	print("app:  %s" % deployed_commit())

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

	_app_tile(user)

	if not frappe.db.exists("Workspace Sidebar", SIDEBAR):
		print("\n*** No %r sidebar on this site. Nothing will appear." % SIDEBAR)
		return
	_what_user_sees(user)


def _app_tile(user):
	"""Whether the app is on the desk at all, which is a separate question.

	A workspace can exist, be public, be visible and be perfectly linked, and the
	app still not appear on the desk -- because frappe/apps.py get_apps() reads
	the add_to_apps_screen hook per installed app and skips any app that has
	none. Nothing about a workspace substitutes for it, and nothing says so.
	"""
	from frappe.apps import get_apps

	hooks = frappe.get_hooks("add_to_apps_screen", app_name=APP) or []
	print("\nDesk app tile")
	if not hooks:
		print("   *** %s declares no add_to_apps_screen hook, so it can never "
		      "appear on the desk however good its workspaces are." % APP)
		return
	route = hooks[0].get("route") or ""
	print("   hook route : %s" % route)

	slug = route.rstrip("/").rsplit("/", 1)[-1]
	target = next((w for w in frappe.get_all(
		"Workspace", fields=["name", "is_hidden", "public"])
		if w.name.lower().replace(" ", "-") == slug), None)
	if not target:
		print("   *** the route points at no workspace on this site.")
	elif target.is_hidden:
		print("   *** %r is HIDDEN, so the tile opens a page nobody should land "
		      "on." % target.name)
	else:
		print("   opens      : %r (visible)" % target.name)

	was = frappe.session.user
	try:
		frappe.set_user(user)
		frappe.clear_cache(user=user)
		shown = APP in [a["name"] for a in get_apps()]
		print("   on the desk for %s: %s" % (user, shown))
		if not shown:
			print("   *** get_apps() also drops an app when the user is not a "
			      "System Manager and the app is in neither the setup-wizard "
			      "completed nor not-required list. And hooks are cached: after "
			      "a deploy the workers must be restarted, not only "
			      "bench clear-cache.")
	finally:
		frappe.set_user(was)


def _what_user_sees(user):
	"""Run boot.py's own two rules against one user, and name what fails.

	It has to BE that user. DeskViews takes no user argument and
	is_item_allowed returns True outright for Administrator before any check
	runs, so asking "what would shadrack see?" while still signed in as
	Administrator answered "everything" every time -- a pass that means nothing,
	for the one question this tool exists to settle.
	"""
	from frappe.desk.desk_views import DeskViews

	was = frappe.session.user
	try:
		frappe.set_user(user)
		frappe.clear_cache(user=user)

		# The same two things boot.py builds before it filters: the workspaces
		# this user may open, and the sidebar document itself.
		views = DeskViews()
		views.build_entities()
		allowed = [d.name for d in (views.workspaces.get("pages") or [])]
		doc = frappe.get_doc("Workspace Sidebar", SIDEBAR)

		print("\nWorkspaces this user may open: %s"
		      % (", ".join(sorted(allowed)) or "NONE"))
		print("\nPer-item filter for %s" % user)
		kept = 0
		for item in doc.items:
			if item.type == "Section Break":
				print("   %-26s (heading, never counts)" % item.label)
				continue
			try:
				ok = doc.is_item_allowed(item.link_to, item.link_type, allowed)
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
			      "survives, so this user sees no Summer Flowers entry at all and "
			      "no error. Give them read on the doctypes above, or a role that "
			      "reaches the workspace.")
		elif user == "Administrator":
			print("*** Administrator passes every check before it runs. Re-run as "
			      "the person who cannot see it; this result proves nothing.")
	finally:
		frappe.set_user(was)


def deployed_commit():
	"""Which commit of this app the site is actually running.

	"Deployed" and "running the code you pushed" are not the same claim, and
	there was no way to tell them apart from inside the site. A button that is
	missing because the deploy predates its fix looks exactly like a button that
	is missing because it was never built.
	"""
	import subprocess

	try:
		import upande_summer_flowers

		root = os.path.dirname(os.path.dirname(upande_summer_flowers.__file__))
		out = subprocess.run(
			["git", "-C", root, "log", "-1", "--format=%h %cs %s"],
			capture_output=True, text=True, timeout=10)
		line = (out.stdout or out.stderr or "").strip()
		dirty = subprocess.run(["git", "-C", root, "status", "--porcelain"],
		                       capture_output=True, text=True, timeout=10).stdout
		return line + ("   (UNCOMMITTED CHANGES PRESENT)" if dirty.strip() else "")
	except Exception as e:
		return "could not read the git commit: %s" % e
