# Copyright (c) 2026, James Kiruga and contributors
"""Put the multiplication back into every route that already exists.

`stages_for()` seeded every generated stage with `yields_per_unit: 1`, and nothing
checked that a stage which ought to multiply actually did. A motherstock that yields
one plant per plantlet is not a motherstock, so a route walked for a purchase said
one TC plantlet becomes 0.855 plants -- and a plan that needed 3,024 plantlets
ordered 136,851, forty-five times over, for every crop raised through a pool.

The quantities are the protocol's now, not the route's, so this re-derives them
rather than asking anyone to type them. Versions are done too: they are immutable
snapshots and the correction would otherwise reach only protocols approved after
today, which is to say none of the ones already being planned against.
"""

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
			# snapshot -- and re-saving a Protocol would drag its whole validate chain
			# through a migrate.
			for r in rows:
				frappe.db.set_value(r.doctype, r.name, {
					"yields_per_unit": r.yields_per_unit,
					"loss_pct": r.loss_pct,
					"is_standing": r.get("is_standing") or 0,
					"yields_per_week": r.get("yields_per_week") or 0,
				}, update_modified=False)
			touched += 1
		print("  %s: re-derived %d route(s)" % (dt, touched))
