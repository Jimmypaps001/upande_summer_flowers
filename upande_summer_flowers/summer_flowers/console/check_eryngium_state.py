"""What actually exists for Eryngium on this site. Read-only."""

import frappe
from frappe.utils import cint


def main():
	vs = [v for v in frappe.get_all("Item", filters={"item_group": ["like", "%"]},
	                                pluck="name", limit_page_length=0)
	      if "eryng" in v.lower()]
	print("Eryngium items: %d" % len(vs))
	for v in vs[:12]:
		print("   %s" % v)

	def count(dt, field="variety"):
		rows = frappe.get_all(dt, fields=["name", field], limit_page_length=0)
		mine = [r for r in rows if "eryng" in str(r.get(field) or "").lower()]
		return mine

	for dt, label in (
		("Summer Flower Market Demand", "market demand"),
		("Summer Flower Production Plan", "production plans"),
		("Summer Flower Procurement Plan", "procurement plans"),
		("Summer Flower Propagation Plan", "propagation plans"),
		("Summer Flower Motherstock Plan", "motherstock plans"),
		("Planting Calendar", "planting calendar entries"),
		("Summer Flower Budget", "budgets"),
	):
		try:
			mine = count(dt)
			print("\n%-28s %d for Eryngium" % (label, len(mine)))
			for r in mine[:6]:
				print("     %-26s %s" % (r.name, r.get("variety")))
		except Exception as e:
			print("\n%-28s could not read: %s" % (label, str(e)[:60]))

	print("\nblocks that carry an Eryngium crop cycle:")
	try:
		cyc = frappe.get_all("Crop Cycle",
		                     fields=["name", "custom_sf_variety", "custom_block"],
		                     limit_page_length=0)
		mine = [c for c in cyc if "eryng" in str(c.get("custom_sf_variety") or "").lower()]
		print("   %d crop cycles" % len(mine))
		for c in mine[:8]:
			print("     %-22s %s" % (c.custom_block, c.custom_sf_variety))
	except Exception as e:
		print("   could not read: %s" % str(e)[:70])

	print("\nthe route on the Eryngium protocols right now:")
	for r in frappe.get_all("Crop Protocol", filters={"custom_is_summer_flower": 1},
	                        fields=["name", "variety"], limit_page_length=0):
		if "eryng" not in (r.variety or "").lower():
			continue
		doc = frappe.get_doc("Crop Protocol", r.name)
		route = " -> ".join("%s%s" % (x.stage, "*" if cint(x.is_purchase) else "")
		                    for x in doc.custom_sf_material_route if x.stage)
		shares = [x.share_pct for x in doc.custom_sf_material_route if x.share_pct]
		print("   %-44s %s   shares=%s" % (r.name[:44], route or "NO ROUTE", shares or "-"))
