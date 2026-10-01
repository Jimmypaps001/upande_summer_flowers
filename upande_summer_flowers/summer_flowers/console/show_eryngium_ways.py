"""Both ways in, as the protocols now hold them. Read-only."""

import frappe
from frappe.utils import cint, flt


def main():
	from upande_summer_flowers.summer_flowers import sourcing

	rows = frappe.get_all("Crop Protocol", filters={"custom_is_summer_flower": 1},
	                      fields=["name", "variety"], limit_page_length=0)
	mine = [r for r in rows if "eryng" in (r.variety or "").lower()
	        or "artemis" in (r.variety or "").lower()]
	print("%-46s %-7s %7s %6s %6s" % ("", "form", "share", "lead", "harden"))
	for r in sorted(mine, key=lambda x: x.name):
		doc = frappe.get_doc("Crop Protocol", r.name)
		ways = sourcing.entry_ways(doc)
		if not ways:
			print("%-46s  no way in" % r.name[:46])
			continue
		first = True
		for w in ways:
			print("%-46s %-7s %6.2f%% %5sw %5sw"
			      % (r.name[:46] if first else "", w["stage"], flt(w["share_pct"]),
			         cint(w["lead_weeks"]), cint(w["weeks_to_ground"])))
			first = False

	print("\nOne order of 100,000 plants for Supernova at Kariki Molo:")
	doc = frappe.get_doc("Crop Protocol", "Eryngium Supernova Questar-Kariki Molo")
	for w in sourcing.entry_ways(doc):
		n = int(round(100000 * flt(w["share_pct"]) / 100.0))
		ahead = cint(w["lead_weeks"]) + cint(w["weeks_to_ground"])
		print("   buy %-6s %8s plants, %2s weeks before the planting week"
		      % (w["stage"], f"{n:,}", ahead))
