"""Create the Summer Flowers Navigation block and hang the workspace off it.

This is the Packhouse workspace's shape: the workspace itself carries one custom
block, and that block is the whole page -- tiles grouped by what someone is
actually doing, not a wall of link cards.

Every route and every count filter is checked against this site before anything
is written, because the block it copies had gone stale in exactly that way.

    bench --site <site> execute ...build_nav.check
    bench --site <site> execute ...build_nav.run --kwargs '{"apply":1}'
"""

import json
import os

import frappe

BLOCK = "Summer Flowers Navigation"
WORKSPACE = "Summer Flowers Planning"
NAV_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nav")


def _source():
	import importlib.util

	spec = importlib.util.spec_from_file_location(
		"sf_build_nav_html", os.path.join(NAV_DIR, "build_nav_html.py"))
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)
	html = mod.render()
	with open(os.path.join(NAV_DIR, "summer_flowers_navigation.html"), "w") as fh:
		fh.write(html)
	return (
		html,
		open(os.path.join(NAV_DIR, "summer_flowers_navigation.css")).read(),
		open(os.path.join(NAV_DIR, "summer_flowers_navigation.js")).read(),
		mod,
	)


def check():
	"""Every tile route and count filter, against this site. Writes nothing."""
	_, _, _, mod = _source()
	bad = 0

	print("routes")
	for label, href in mod.routes():
		path = href.split("?")[0].split("#")[0].strip("/")
		if path.startswith("app/"):
			slug = path[4:]
			dt = frappe.db.get_value("DocType", {"name": ["like", "%"]}, "name")  # touch db
			hits = [d for d in frappe.get_all("DocType", pluck="name")
			        if frappe.scrub(d).replace("_", "-") == slug]
			ok = bool(hits)
			what = hits[0] if hits else "no doctype for /%s" % slug
		else:
			ok = bool(frappe.db.exists("Web Page", {"route": path})) or _is_www_page(path)
			what = "page /%s" % path
		if not ok:
			bad += 1
		print("   %s %-26s %s" % ("ok  " if ok else "MISS", label, what))

	print("\ncounts")
	for label, dt, filters in mod.counts():
		if not frappe.db.exists("DocType", dt):
			print("   MISS %-26s no doctype %r" % (label, dt))
			bad += 1
			continue
		try:
			n = frappe.db.count(dt, filters or {})
			print("   ok   %-26s %s -> %s" % (label, dt, n))
		except Exception as e:
			bad += 1
			print("   BAD  %-26s %s -> %s" % (label, dt, str(e)[:90]))

	print("\n%d problem(s)" % bad)
	return bad


def _is_www_page(path):
	for app in frappe.get_installed_apps():
		try:
			base = frappe.get_app_path(app, "www")
		except Exception:
			continue
		for ext in (".html", ".md"):
			if os.path.exists(os.path.join(base, path + ext)):
				return True
	return False


def run(apply=0, workspace=WORKSPACE, keep_cards=0):
	from frappe.utils import cint
	apply, keep_cards = cint(apply), cint(keep_cards)

	bad = check()
	if bad and apply:
		print("\nrefusing to write with %d broken tile(s)" % bad)
		return

	html, css, js, _mod = _source()
	print("\nblock %r: %d chars html, %d css, %d js" % (BLOCK, len(html), len(css), len(js)))
	if not frappe.db.exists("Workspace", workspace):
		print("no workspace %r -- run make_ui_workspace first" % workspace)
		return
	if not apply:
		print("   (dry run -- pass apply=1 to write it)")
		return

	doc = (frappe.get_doc("Custom HTML Block", BLOCK)
	       if frappe.db.exists("Custom HTML Block", BLOCK)
	       else frappe.new_doc("Custom HTML Block"))
	doc.name = BLOCK
	doc.html, doc.style, doc.script = html, css, js
	doc.private = 0
	doc.flags.ignore_permissions = True
	doc.save() if not doc.is_new() else doc.insert()

	ws = frappe.get_doc("Workspace", workspace)
	ws.set("custom_blocks", [])
	ws.append("custom_blocks", {"custom_block_name": BLOCK, "label": BLOCK})
	content = [{"id": "sfnav", "type": "custom_block",
	            "data": {"custom_block_name": BLOCK, "col": 12}}]
	if keep_cards:
		# The link cards, kept underneath for anyone who navigates by doctype name.
		content += [b for b in json.loads(ws.content or "[]") if b.get("type") == "card"]
	ws.content = json.dumps(content)
	ws.flags.ignore_permissions = True
	ws.flags.ignore_links = True
	ws.save()
	frappe.db.commit()

	ws = frappe.get_doc("Workspace", workspace)
	print("\n   %r -> %d content block(s): %s"
	      % (workspace, len(json.loads(ws.content or "[]")),
	         [b["type"] for b in json.loads(ws.content or "[]")]))
