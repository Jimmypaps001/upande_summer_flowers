"""Build the Summer Flowers workspace as a plain record, not as app code.

The app ships one through summer_flowers/workspace/summer_flowers/, which is
synced from that JSON on every migrate -- so a site that will not take the
deploy will not take the workspace either, and editing it on the site is undone
the next time anyone migrates.

This makes a second one that the app does not own: no module, so the workspace
sync never looks at it, and it can be edited in the UI like any other. It is
copied from whatever the shipped one currently holds, so the two start
identical and the old one can simply be hidden.

    bench --site <site> execute ...make_ui_workspace.run --kwargs '{"apply":1}'
    bench --site <site> execute ...make_ui_workspace.run --kwargs '{"apply":1,"hide_original":1}'
"""

import json

import frappe

SOURCE = "Summer Flowers"
TARGET = "Summer Flowers Planning"


def run(apply=0, hide_original=0, source=SOURCE, target=TARGET):
	from frappe.utils import cint
	apply, hide_original = cint(apply), cint(hide_original)

	if not frappe.db.exists("Workspace", source):
		print("no workspace called %r on this site" % source)
		print("workspaces here:", frappe.get_all("Workspace", pluck="name")[:20])
		return
	src = frappe.get_doc("Workspace", source)
	print("source %r: %d links, %d shortcuts, module %r, public %s"
	      % (source, len(src.links), len(src.shortcuts), src.module, src.public))

	existing = frappe.db.exists("Workspace", target)
	print("target %r: %s" % (target, "exists, will be refreshed" if existing
	                         else "will be created"))
	if not apply:
		print("\n   (dry run -- pass apply=1 to write it)")
		return

	doc = frappe.get_doc("Workspace", target) if existing \
		else frappe.new_doc("Workspace")
	doc.name = target
	doc.label = target
	doc.title = target
	# No module. That is the whole point: frappe's workspace sync only rebuilds
	# workspaces an app owns, so leaving this blank means a migrate cannot touch it
	# and somebody can rearrange it in the UI and have it stay rearranged.
	doc.module = None
	doc.public = 1
	doc.icon = src.icon or "leaf"
	doc.indicator_color = src.indicator_color
	# Just before the shipped one, so it reads first in the sidebar.
	doc.sequence_id = (src.sequence_id or 90) - 1
	doc.content = src.content
	doc.is_hidden = 0

	for table, rows in (("links", src.links), ("shortcuts", src.shortcuts),
	                    ("charts", src.charts), ("number_cards", src.number_cards),
	                    ("quick_lists", src.quick_lists),
	                    ("custom_blocks", src.custom_blocks),
	                    ("roles", src.roles)):
		doc.set(table, [])
		for r in rows:
			row = {k: v for k, v in r.as_dict().items()
			       if k not in ("name", "parent", "parenttype", "parentfield",
			                    "owner", "creation", "modified", "modified_by",
			                    "idx", "docstatus")}
			doc.append(table, row)

	doc.flags.ignore_permissions = True
	doc.flags.ignore_links = True
	if existing:
		doc.save()
	else:
		doc.insert()

	if hide_original:
		# Hidden, not deleted: the sidebar fixture still points "Home" at it, and a
		# deleted workspace would take that link with it.
		frappe.db.set_value("Workspace", source, "is_hidden", 1)
		print("   %r hidden" % source)

	frappe.db.commit()
	d = frappe.get_doc("Workspace", target)
	print("\n   %r written: %d links, %d shortcuts, module %r, sequence %s"
	      % (target, len(d.links), len(d.shortcuts), d.module, d.sequence_id))
	print("   cards: %s"
	      % [l.label for l in d.links if l.type == "Card Break"])


def payload(source=SOURCE, target=TARGET):
	"""The same workspace as JSON, for creating it on a site over the REST API."""
	src = frappe.get_doc("Workspace", source)
	out = {
		"doctype": "Workspace", "name": target, "label": target, "title": target,
		"module": None, "public": 1, "icon": src.icon or "leaf",
		"indicator_color": src.indicator_color,
		"sequence_id": (src.sequence_id or 90) - 1,
		"content": src.content, "is_hidden": 0,
	}
	for table, rows in (("links", src.links), ("shortcuts", src.shortcuts),
	                    ("roles", src.roles)):
		out[table] = [{k: v for k, v in r.as_dict().items()
		               if k not in ("name", "parent", "parenttype", "parentfield",
		                            "owner", "creation", "modified", "modified_by",
		                            "docstatus")} for r in rows]
	print(json.dumps(out, indent=1, default=str))
	return out


def repoint_sidebar(apply=0, source=SOURCE, target=TARGET, sidebar="Summer Flowers"):
	"""Send the app sidebar's Home at the new workspace.

	The sidebar is a shipped fixture whose first item is Home -> Workspace
	"Summer Flowers". Hiding that workspace without moving this leaves Home
	pointing at something nobody can find in the list any more.
	"""
	from frappe.utils import cint
	apply = cint(apply)

	if not frappe.db.exists("Workspace Sidebar", sidebar):
		print("no Workspace Sidebar called %r" % sidebar)
		print("sidebars here:", frappe.get_all("Workspace Sidebar", pluck="name"))
		return
	doc = frappe.get_doc("Workspace Sidebar", sidebar)
	hits = [r for r in doc.items
	        if r.link_type == "Workspace" and r.link_to == source]
	if not hits:
		already = [r.label for r in doc.items
		           if r.link_type == "Workspace" and r.link_to == target]
		print("nothing points at %r%s"
		      % (source, (" (already on %r: %s)" % (target, already)) if already else ""))
		return
	for r in hits:
		print("%s item %r: %s -> %s"
		      % ("repointing" if apply else "would repoint", r.label, source, target))
		if apply:
			r.link_to = target
	if apply:
		doc.flags.ignore_permissions = True
		doc.flags.ignore_links = True
		doc.save()
		frappe.db.commit()
		print("   saved")
	else:
		print("\n   (dry run -- pass apply=1)")
