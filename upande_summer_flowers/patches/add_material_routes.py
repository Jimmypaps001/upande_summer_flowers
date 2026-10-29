"""Routes become records, and every protocol lists its ways in.

Seeds a Material Route per route the app used to hard-code, then gives each
summer flower protocol its ways (route, share, lead, plants per unit, price) and
the weeks at each stage, read off the route it already has. Written straight to
the child tables: no protocol is re-saved, re-versioned or sent back through its
workflow.
"""

import frappe


def execute():
	for dt in ("material_route_stage", "material_route", "crop_protocol_way",
	           "crop_protocol_way_stage"):
		frappe.reload_doc("summer_flowers", "doctype", dt)

	from upande_summer_flowers.summer_flowers.crop_routes import SEED_ROUTES

	for name, stages in SEED_ROUTES.items():
		if not frappe.db.exists("Material Route", name):
			frappe.get_doc({"doctype": "Material Route", "route_name": name,
			                "stages": [{"stage": s} for s in stages]}).insert(ignore_permissions=True)
	frappe.local._sf_routes = None

	if not frappe.db.has_column("Crop Protocol", "custom_is_summer_flower"):
		return
	from upande_summer_flowers.patches._sf_customisations import ensure

	if not ensure("Crop Protocol", "custom_sf_ways", "custom_sf_way_stages"):
		return
	from upande_summer_flowers.summer_flowers import route_ways as rw

	done = set(frappe.get_all("Crop Protocol Way", filters={"parenttype": "Crop Protocol"},
	                          pluck="parent", distinct=True))
	for name in frappe.get_all("Crop Protocol", filters={"custom_is_summer_flower": 1},
	                           pluck="name"):
		if name in done:
			continue
		doc = frappe.get_doc("Crop Protocol", name)
		if not doc.get(rw.ROUTE_FIELD):
			continue
		rw.ways_from_route(doc)
		# Same stage rows, with the ones the protocol works out itself marked.
		rw.fill_way_stages(doc)
		for field in (rw.WAYS, rw.WAY_STAGES):
			for i, row in enumerate(doc.get(field), start=1):
				row.idx = i
				row.db_insert()
