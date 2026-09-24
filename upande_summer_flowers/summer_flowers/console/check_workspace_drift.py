"""Does the app ship what this bench actually shows?"""

import json
import os

import frappe


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())


def _run():
	app = frappe.get_app_path("upande_summer_flowers")

	# ---- Workspace Sidebar
	path = os.path.join(app, "fixtures", "workspace_sidebar.json")
	shipped = json.load(open(path))[0]
	live = frappe.get_all("Workspace Sidebar",
	                      filters={"app": "upande_summer_flowers"}, pluck="name")
	print("Workspace Sidebar records here: %s" % live)
	if not live:
		print("   none on this bench")
	else:
		d = frappe.get_doc("Workspace Sidebar", live[0])
		a = [(i.label, i.link_type, i.link_to or i.url) for i in d.items]
		b = [(i["label"], i["link_type"], i.get("link_to") or i.get("url"))
		     for i in shipped["items"]]
		print("   bench %d items, shipped %d items, same: %s"
		      % (len(a), len(b), a == b))
		only_here = [x for x in a if x not in b]
		only_shipped = [x for x in b if x not in a]
		for x in only_here:
			print("      on the bench but NOT shipped: %s" % (x,))
		for x in only_shipped:
			print("      shipped but NOT on the bench: %s" % (x,))

	# ---- Workspace
	wpath = os.path.join(app, "summer_flowers", "workspace", "summer_flowers",
	                     "summer_flowers.json")
	ws = json.load(open(wpath))
	print("\nWorkspace 'Summer Flowers'")
	if not frappe.db.exists("Workspace", "Summer Flowers"):
		print("   no Workspace record on this bench")
		return
	w = frappe.get_doc("Workspace", "Summer Flowers")
	a = [(l.label, l.link_type, l.link_to, l.type) for l in w.links]
	b = [(l.get("label"), l.get("link_type"), l.get("link_to"), l.get("type"))
	     for l in (ws.get("links") or [])]
	print("   bench %d links, shipped %d links, same: %s" % (len(a), len(b), a == b))
	for x in [y for y in a if y not in b]:
		print("      on the bench but NOT shipped: %s" % (x,))
	for x in [y for y in b if y not in a]:
		print("      shipped but NOT on the bench: %s" % (x,))
	for f in ("content", "sequence_id", "public", "module"):
		sv, lv = ws.get(f), w.get(f)
		if f == "content":
			same = json.loads(sv or "[]") == json.loads(lv or "[]")
			print("   content same: %s" % same)
		elif sv != lv:
			print("   %s differs: shipped=%r bench=%r" % (f, sv, lv))
