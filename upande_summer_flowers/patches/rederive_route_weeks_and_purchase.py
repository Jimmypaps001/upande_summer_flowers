# Copyright (c) 2026, James Kiruga and contributors
"""Work out what each route stage is and how long it takes, instead of asking.

is_purchase and weeks were typed on the route. Nobody ticked is_purchase -- it is
a box confirming that tissue culture is bought, which is a question with one
answer -- and a procurement plan refused to build without it, on a protocol whose
route was plainly there. The weeks were the tray and pot weeks written a second
time, free to disagree with the protocol they came from.

Both are derived now, so both are re-derived here: on every protocol, and on the
versions already approved, which are snapshots and would otherwise never hear.
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
			rows = doc.get("material_route") or doc.get("custom_sf_material_route")
			if not rows:
				continue
			if dt == "Crop Protocol" and not is_summer_flower(doc):
				continue
			before = [(r.is_purchase, r.weeks) for r in rows]
			set_route_quantities(doc, native=native)
			rows = doc.get("material_route") or doc.get("custom_sf_material_route")
			if before == [(r.is_purchase, r.weeks) for r in rows]:
				continue
			for r in rows:
				frappe.db.set_value(r.doctype, r.name, {
					"is_purchase": r.is_purchase or 0,
					"weeks": r.weeks or 0,
					"yields_per_unit": r.yields_per_unit,
					"loss_pct": r.loss_pct,
					"is_standing": r.get("is_standing") or 0,
					"yields_per_week": r.get("yields_per_week") or 0,
				}, update_modified=False)
			touched += 1
		print("  %s: route stages re-derived on %d" % (dt, touched))
