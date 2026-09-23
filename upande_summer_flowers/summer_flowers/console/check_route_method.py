"""What each protocol's route says about how its material is got."""

import frappe


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())


def _run():
	from upande_summer_flowers.summer_flowers import sourcing

	rows = frappe.get_all("Crop Protocol Version",
	                      filters={"version_status": "Active"},
	                      fields=["name", "variety", "farm"], limit=400)
	seen = {}
	print("%-46s %-10s %-16s %-4s %s"
	      % ("protocol version", "method", "bought as", "prop", "raised here through"))
	for r in rows:
		v = frappe.get_cached_doc("Crop Protocol Version", r.name)
		d = sourcing.route_plan(v)
		if not d["has_route"]:
			seen["no route"] = seen.get("no route", 0) + 1
			continue
		key = (d["method"], bool(d["propagates"]), tuple(d["in_house"]))
		seen[key] = seen.get(key, 0) + 1
		if seen[key] <= 2:
			print("%-46s %-10s %-16s %-4s %s"
			      % (r.name[:46], d["method"] or "UNSET", d["entry_stage"] or "-",
			         "yes" if d["propagates"] else "no", ", ".join(d["in_house"]) or "-"))
	print("\nshapes found:")
	for k, n in sorted(seen.items(), key=lambda x: -x[1]):
		print("   %-4s  %s" % (n, k))
