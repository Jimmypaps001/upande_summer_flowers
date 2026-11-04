# Copyright (c) 2026, James Kiruga and contributors
"""Tell existing procurement plans how their material is got."""

import frappe


def execute():
	from upande_summer_flowers.summer_flowers import sourcing

	names = frappe.get_all("Summer Flower Procurement Plan",
	                       filters={"docstatus": ["<", 2]}, pluck="name")
	fixed = 0
	for name in names:
		d = frappe.db.get_value("Summer Flower Procurement Plan", name,
		                        ["protocol", "method", "entry_stage",
		                         "propagates_here"], as_dict=True)
		if not (d and d.protocol) or not frappe.db.exists("Crop Protocol Version",
		                                                  d.protocol):
			continue
		v = frappe.get_cached_doc("Crop Protocol Version", d.protocol)
		decided = sourcing.route_plan(v)
		if not decided["has_route"]:
			continue
		values = {
			"propagates_here": 1 if decided["propagates"] else 0,
			"in_house_stages": ", ".join(decided["in_house"]) or None,
			"route_verdict": decided["reason"],
		}
		# The stage bought was never recorded on a plan raised before the route was
		if decided["method"] == "Purchase" and not d.entry_stage:
			values["entry_stage"] = decided["entry_stage"]
			values["method"] = "Purchase"
		frappe.db.set_value("Summer Flower Procurement Plan", name, values,
		                    update_modified=False)
		fixed += 1
	print("  procurement plans given their route: %d of %d" % (fixed, len(names)))
