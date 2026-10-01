"""How is Eryngium's material route modelled here? Read-only."""

import frappe
from frappe.utils import cint


def main():
	from upande_summer_flowers.summer_flowers import crop_protocol as cp
	from upande_summer_flowers.summer_flowers import crop_routes as cr

	print("ROUTE_ENTRY  (a route may start here): %s" % (cp.ROUTE_ENTRY,))
	print("BOUGHT_STAGES (ordered from outside) : %s" % (cp.BOUGHT_STAGES,))
	print("TAKEN_STAGES  (off our own crop)     : %s" % (cp.TAKEN_STAGES,))
	print("\nroutes that mention Roots:")
	for name, stages in cr.ROUTES.items():
		if any("Root" in s for s in stages):
			print("   %-24s %s" % (name, " -> ".join(stages)))

	print("\nEryngium protocols on this site:")
	rows = frappe.get_all("Crop Protocol",
	                      filters={"custom_is_summer_flower": 1,
	                               "variety": ["like", "%Eryngium%"]},
	                      fields=["name", "variety", "farm",
	                              "custom_sf_protocol_status"],
	                      limit_page_length=0)
	if not rows:
		rows = frappe.get_all("Crop Protocol",
		                      filters={"custom_is_summer_flower": 1},
		                      fields=["name", "variety", "farm",
		                              "custom_sf_protocol_status"],
		                      limit_page_length=0)
		rows = [r for r in rows if "eryng" in (r.variety or "").lower()]
	for r in rows:
		doc = frappe.get_doc("Crop Protocol", r.name)
		route = [(x.stage, cint(x.weeks)) for x in (doc.get("custom_sf_material_route") or [])]
		print("   %-38s %-14s %s" % (r.name[:38], r.custom_sf_protocol_status,
		                             " -> ".join("%s(%sw)" % s for s in route) or "NO ROUTE"))

	print("\nWhat the Eryngium routes actually say, stage by stage:")
	tbl = "tabCrop Material Stage"
	try:
		hits = frappe.db.sql(
			"select parent, stage, weeks from `%s` where stage like %%s" % tbl,
			("%Root%",), as_dict=True)
	except Exception as e:
		print("   (route child table not readable: %s)" % str(e)[:60])
		return
	if not hits:
		print("   no protocol anywhere has a Roots stage")
	for h in hits[:20]:
		print("   %-42s %-16s %sw" % (h.parent[:42], h.stage, h.weeks))
