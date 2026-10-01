"""A protocol bought two ways: tissue culture and roots. Rolled back.

The numbers are the farm's own, off the Eryngium delivery tracker:
TC is 7 weeks of supplier lead and 7 weeks of hardening; roots are 15 and 15.
In the 2026 order the two run together -- Supernova 622,386 as TC against
135,000 as roots -- which is 82/18.
"""

import frappe
from frappe.utils import cint, flt

TC_LEAD, TC_HARDEN = 7, 7
ROOT_LEAD, ROOT_HARDEN = 15, 15


def main(protocol="Eryngium Magnetar Questar-Carzan MR"):
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.crop_protocol import route_chains

	doc = frappe.get_doc("Crop Protocol", protocol)
	print("%s  (%s at %s)" % (protocol, doc.variety, doc.farm))
	print("  route as it stands: %s"
	      % " -> ".join(r.stage for r in doc.custom_sf_material_route if r.stage))

	doc.set("custom_sf_material_route", [])
	for stage, weeks, buy, lead, share in (
		("TC", TC_HARDEN, 1, TC_LEAD, 82),
		("Propagation", 0, 0, 0, 0),
		("Plants", 0, 0, 0, 0),
		("Roots", ROOT_HARDEN, 1, ROOT_LEAD, 18),
		("Propagation", 0, 0, 0, 0),
		("Plants", 0, 0, 0, 0),
	):
		doc.append("custom_sf_material_route", {
			"row_type": "Stage", "stage": stage, "weeks": weeks,
			"is_purchase": buy, "lead_weeks": lead,
			"share_pct": share or None,
		})
	doc.flags.ignore_permissions = True
	doc.save()
	doc.reload()

	chains = route_chains(doc)
	print("\n  saved. %d chain(s):" % len(chains))
	for c in chains:
		rows = [r for r, _s in c]
		print("     %-46s share %s%%  lead %sw"
		      % (" -> ".join(r.stage for r in rows),
		         flt(rows[0].get("share_pct")) or "-", cint(rows[0].get("lead_weeks"))))

	# Read straight off the protocol: a version is a snapshot and refuses to be
	# edited, which is correct and is why this asks the editable document.
	print("\n  rows as saved:")
	for r in doc.custom_sf_material_route:
		print("     %-14s weeks=%-3s is_purchase=%-2s lead=%-3s share=%s"
		      % (r.stage, r.weeks, r.is_purchase, r.lead_weeks, r.share_pct))
	ways = sourcing.entry_ways(doc)
	if ways:
		print("\n  what sourcing reads off it:")
		for wy in ways:
			print("     buy %-7s %5s%% of the order · lead %2sw · %sw to ground · then %s"
			      % (wy["stage"], wy["share_pct"], wy["lead_weeks"],
			         wy["weeks_to_ground"], ", ".join(wy["in_house"]) or "straight out"))
		need = 757386
		print("\n  a 757,386 plant order splits:")
		for wy in ways:
			n = int(round(need * flt(wy["share_pct"]) / 100.0))
			print("     %-7s %9s plants, ordered %s weeks before the sticking week"
			      % (wy["stage"], f"{n:,}", wy["lead_weeks"] + wy["weeks_to_ground"]))

	print("\n  now a bad split, to see it refused:")
	doc.custom_sf_material_route[3].share_pct = 30
	try:
		doc.save()
		print("     !! 82 + 30 was accepted")
	except Exception as e:
		print("     refused: %s" % str(e)[:140])
	frappe.db.rollback()
	print("\n  (rolled back -- nothing kept)")
