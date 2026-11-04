# Copyright (c) 2026, James Kiruga and contributors
"""Put the multiplication back into every route that already exists."""

import frappe

from upande_summer_flowers.summer_flowers.crop_protocol import (
	is_summer_flower,
	set_route_quantities,
)


def execute():
	for dt, native in (("Crop Protocol", False), ("Crop Protocol Version", True)):
		if not frappe.db.exists("DocType", dt):
			continue
		touched = 0
		for name in frappe.get_all(dt, pluck="name"):
			doc = frappe.get_doc(dt, name)
			if not (doc.get("material_route") or doc.get("custom_sf_material_route")):
				continue
			if dt == "Crop Protocol" and not is_summer_flower(doc):
				continue
			before = [(r.yields_per_unit, r.loss_pct, r.get("is_standing"))
			          for r in (doc.get("material_route")
			                    or doc.get("custom_sf_material_route"))]
			set_route_quantities(doc, native=native)
			rows = doc.get("material_route") or doc.get("custom_sf_material_route")
			after = [(r.yields_per_unit, r.loss_pct, r.get("is_standing")) for r in rows]
			if before == after:
				continue
			# db_set on the children: a Version refuses to be saved at all -- it is a
			for r in rows:
				frappe.db.set_value(r.doctype, r.name, {
					"yields_per_unit": r.yields_per_unit,
					"loss_pct": r.loss_pct,
					"is_standing": r.get("is_standing") or 0,
					"yields_per_week": r.get("yields_per_week") or 0,
				}, update_modified=False)
			touched += 1
		print("  %s: re-derived %d route(s)" % (dt, touched))
