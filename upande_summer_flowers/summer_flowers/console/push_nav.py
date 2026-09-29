"""Put the navigation workspace on a remote site over REST.

For a site where migrate is not the way the change gets there. It sends the
Custom HTML Block and the "Summer Flowers Planning" workspace, hides the
module-owned "Summer Flowers", and repoints the sidebar's Home item -- the same
four writes build_nav.run and make_ui_workspace make locally.

    export SF_SITE=https://<site>
    export SF_TOKEN=<api_key>:<api_secret>
    .../env/bin/python push_nav.py            # dry run: says what it would write
    .../env/bin/python push_nav.py --apply
"""

import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(os.path.dirname(HERE), "nav")
BLOCK = "Summer Flowers Navigation"
WORKSPACE = "Summer Flowers Planning"
OLD = "Summer Flowers"
SIDEBAR = "Summer Flowers"


def call(site, token, method, path, body=None):
	req = urllib.request.Request(
		site.rstrip("/") + path, method=method,
		data=json.dumps(body).encode() if body is not None else None,
		headers={"Authorization": "token " + token,
		         "Content-Type": "application/json", "Accept": "application/json"})
	try:
		with urllib.request.urlopen(req, timeout=90) as r:
			return r.status, json.loads(r.read().decode() or "{}")
	except urllib.error.HTTPError as e:
		return e.code, (e.read().decode()[:400] or "")


def block_payload():
	sys.path.insert(0, NAV)
	import build_nav_html

	return {
		"doctype": "Custom HTML Block", "name": BLOCK, "private": 0,
		"html": build_nav_html.render(),
		"style": open(os.path.join(NAV, "summer_flowers_navigation.css")).read(),
		"script": open(os.path.join(NAV, "summer_flowers_navigation.js")).read(),
	}


def workspace_payload():
	rows = json.load(open(os.path.join(
		os.path.dirname(os.path.dirname(HERE)), "fixtures", "workspace.json")))
	ws = next(r for r in rows if r["name"] == WORKSPACE)
	ws["doctype"] = "Workspace"
	return ws


def main():
	site, token = os.environ.get("SF_SITE"), os.environ.get("SF_TOKEN")
	apply = "--apply" in sys.argv
	if not site or not token:
		print("set SF_SITE and SF_TOKEN"); return 2

	st, who = call(site, token, "GET", "/api/method/frappe.auth.get_logged_user")
	print("%s -> %s %s" % (site, st, who))
	if st != 200:
		return 1

	block, ws = block_payload(), workspace_payload()
	print("\nwould write:")
	print("  Custom HTML Block %r  (%d html, %d css, %d js)"
	      % (BLOCK, len(block["html"]), len(block["style"]), len(block["script"])))
	print("  Workspace %r  (%d links, %d shortcuts, %d content blocks)"
	      % (WORKSPACE, len(ws.get("links") or []), len(ws.get("shortcuts") or []),
	         len(json.loads(ws.get("content") or "[]"))))
	print("  Workspace %r -> is_hidden 1" % OLD)
	print("  Workspace Sidebar %r Home -> %r" % (SIDEBAR, WORKSPACE))
	if not apply:
		print("\n   (dry run -- pass --apply)")
		return 0

	for payload, name in ((block, BLOCK), (ws, WORKSPACE)):
		dt = payload["doctype"].replace(" ", "%20")
		st, _ = call(site, token, "GET", "/api/resource/%s/%s"
		             % (dt, urllib.request.quote(name)))
		if st == 200:
			st, r = call(site, token, "PUT", "/api/resource/%s/%s"
			             % (dt, urllib.request.quote(name)), payload)
			print("  update %-28s %s" % (name, st if st == 200 else r))
		else:
			st, r = call(site, token, "POST", "/api/resource/%s" % dt, payload)
			print("  create %-28s %s" % (name, st if st in (200, 201) else r))

	st, r = call(site, token, "PUT", "/api/resource/Workspace/%s"
	             % urllib.request.quote(OLD), {"is_hidden": 1})
	print("  hide   %-28s %s" % (OLD, st if st == 200 else r))

	st, r = call(site, token, "GET", "/api/resource/Workspace%%20Sidebar/%s"
	             % urllib.request.quote(SIDEBAR))
	if st == 200:
		items = r["data"].get("items") or []
		moved = 0
		for it in items:
			if it.get("link_type") == "Workspace" and it.get("link_to") == OLD:
				it["link_to"] = WORKSPACE
				moved += 1
		if moved:
			st, r = call(site, token, "PUT", "/api/resource/Workspace%%20Sidebar/%s"
			             % urllib.request.quote(SIDEBAR), {"items": items})
			print("  sidebar Home -> %-13s %s" % (WORKSPACE, st if st == 200 else r))
		else:
			print("  sidebar already points at %r" % WORKSPACE)
	else:
		print("  sidebar %r not found (%s)" % (SIDEBAR, st))
	return 0


if __name__ == "__main__":
	sys.exit(main())
