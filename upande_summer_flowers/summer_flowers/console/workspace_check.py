# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Why a site's Summer Flowers workspace does not look like the one it shipped with.

    bench --site <site> execute \\
        upande_summer_flowers.summer_flowers.console.workspace_check.main

The workspace and its sidebar travel with the app -- the workspace as a module
JSON that migrate syncs, the sidebar as a fixture. Both can land intact and still
render as little as the dashboard section, because Frappe silently drops a link
whose doctype is not installed or whose user has no read permission on it.

Half of what this workspace points at belongs to other apps: Bed to upande_core,
Block, Propagation Batch and Seedling Request to upande_propagation, Crop Cycle
and Crop Protocol to upande_agriculture. On a site without those apps every one of
those links disappears and only the dashboard URLs are left, which looks exactly
like a workspace that failed to arrive.

This compares what the site has against what the app ships and names the reason
for each missing piece, so the answer is "install upande_propagation" rather than
"push the workspace again". It writes nothing.
"""

import json

import frappe


def _shipped():
	"""The workspace and sidebar as the app ships them, not as the site holds them."""
	ws = json.load(open(frappe.get_app_path(
		"upande_summer_flowers", "summer_flowers", "workspace",
		"summer_flowers", "summer_flowers.json")))
	side = json.load(open(frappe.get_app_path(
		"upande_summer_flowers", "fixtures", "workspace_sidebar.json")))
	return ws, (side[0] if side else {})


def _why_missing(dt):
	"""Why a doctype link would not render here."""
	if not frappe.db.exists("DocType", dt):
		return "doctype not installed"
	module = frappe.db.get_value("DocType", dt, "module")
	app = frappe.db.get_value("Module Def", module, "app_name")
	if app and app not in frappe.get_installed_apps():
		return "app %s not installed" % app
	if not frappe.has_permission(dt, "read"):
		return "no read permission for %s" % frappe.session.user
	return None


def main():
	ws_file, side_file = _shipped()

	print("=" * 72)
	print("WORKSPACE")
	print("=" * 72)
	if not frappe.db.exists("Workspace", "Summer Flowers"):
		print("  The Workspace record does not exist on this site.")
		print("  migrate syncs it from the module JSON; if it is absent, check that the")
		print("  Module Def 'Summer Flowers' has app_name=upande_summer_flowers and")
		print("  custom=0 -- a module marked custom is skipped by the sync.")
	else:
		ws = frappe.get_doc("Workspace", "Summer Flowers")
		print("  label=%s public=%s hidden=%s" % (ws.label, ws.public, ws.is_hidden))
		print("  links     site %-3d   shipped %d" % (len(ws.links or []), len(ws_file.get("links") or [])))
		print("  shortcuts site %-3d   shipped %d" % (len(ws.shortcuts or []), len(ws_file.get("shortcuts") or [])))
		print("  content   %s" % ("same as shipped" if (ws.content or "") == (ws_file.get("content") or "")
		                          else "DIFFERS from shipped -- the site has edited it"))
		md = frappe.db.get_value("Module Def", "Summer Flowers",
		                         ["app_name", "custom"], as_dict=True)
		print("  Module Def: app_name=%s custom=%s%s"
		      % (md.app_name if md else "?", md.custom if md else "?",
		         "   <-- custom=1 stops migrate syncing the workspace" if md and md.custom else ""))

	print("\n" + "=" * 72)
	print("SIDEBAR")
	print("=" * 72)
	row = frappe.db.get_value("Workspace Sidebar", {"title": "Summer Flowers"}, "name")
	if not row:
		print("  No Workspace Sidebar titled 'Summer Flowers'.")
		print("  It ships as a fixture, so run: bench --site <site> migrate")
	else:
		live = frappe.get_doc("Workspace Sidebar", row)
		print("  items  site %-3d   shipped %d"
		      % (len(live.items or []), len(side_file.get("items") or [])))
	dupes = frappe.db.sql("""select name from `tabWorkspace Sidebar`
	                         where title like '%%Summer%%Flowers%%'""", pluck=True)
	if len(dupes) > 1:
		print("  more than one sidebar matches: %s" % dupes)
		print("  a near-duplicate title can shadow the real one in the menu")

	print("\n" + "=" * 72)
	print("WHAT WOULD NOT RENDER HERE")
	print("=" * 72)
	wanted = sorted({l.get("link_to") for l in (ws_file.get("links") or [])
	                 if l.get("link_type") == "DocType" and l.get("link_to")}
	                | {s.get("link_to") for s in (ws_file.get("shortcuts") or [])
	                   if s.get("type") == "DocType" and s.get("link_to")}
	                | {i.get("link_to") for i in (side_file.get("items") or [])
	                   if i.get("link_type") == "DocType" and i.get("link_to")})
	bad = 0
	for dt in wanted:
		why = _why_missing(dt)
		if why:
			bad += 1
			print("  %-38s %s" % (dt, why))
	if not bad:
		print("  Nothing. Every doctype the workspace points at is installed and readable,")
		print("  so the workspace here should look exactly as it was built.")
	else:
		print("\n  %d of %d links would be hidden. Frappe drops them without a message,"
		      % (bad, len(wanted)))
		print("  which leaves the dashboard section and looks like a missing workspace.")

	print("\n" + "=" * 72)
	print("APPS")
	print("=" * 72)
	installed = frappe.get_installed_apps()
	for app in ("upande_summer_flowers", "upande_agriculture", "upande_propagation",
	            "upande_core", "erpnext"):
		print("  %-26s %s" % (app, "installed" if app in installed else "*** NOT INSTALLED ***"))
