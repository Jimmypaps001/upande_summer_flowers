"""Put the Eryngium tracker's own figures on the Eryngium protocols.

Everything here is read off two workbooks the farm keeps, not invented:

  Eryngium Seedlings delivery tracking, ASSUMPTIONS sheet
      Iribov/TC, Stokman/TC, Vitroflora/TC and Roots/Roots, with grade 1
      hardening of 7-8 weeks for tissue culture and 15 for roots.
  Eryngium Seedlings delivery tracking, Delivery and Propagation Data
      order week to delivery week: exactly 7 weeks across 410 tissue culture
      orders, exactly 15 across 37 root orders.
  Eryngium Farm Orders, 2026 Farms Order
      each variety's order split across Iribov, Stokman and Roots. The share
      each way is that split, normalised.

The farm codes come from the first cell of each farm sheet in the tracker, not
from guessing which Kariki is which:  MR Carzan MR, KN Kariki Naivasha,
BT Kariki Nanyuki, KD Kariki Molo.

    bench --site <site> execute ...load_eryngium_routes.main
    bench --site <site> execute ...load_eryngium_routes.main --kwargs '{"apply":1}'
"""

import frappe
from frappe.utils import cint, flt

TC_LEAD, TC_HARDEN = 7, 7
ROOT_LEAD, ROOT_HARDEN = 15, 15

# variety -> (tissue culture plants, root plants) from the 2026 Farms Order
ORDER_2026 = {
	"Supernova": (414386 + 208000, 135000),
	"Orion": (253000 + 0, 255000),
	"Aquarius": (117239 + 260000, 110481),
	"Magnetar": (86108 + 195000, 237488),
	"Sirius": (170582 + 65000, 112253),
	"Scorpius": (48230 + 65000, 39305),
	"Artemis": (81090 + 0, 37978),
	"Gemini": (81090 + 65000, 37457),
}


def _short(variety):
	"""The tracker's short name for a protocol's variety, or None."""
	low = (variety or "").lower()
	for short in ORDER_2026:
		if short.lower() in low:
			return short
	# The tracker writes Gemini as Germini on some sheets.
	if "germini" in low or "gemini" in low:
		return "Gemini"
	return None


def shares(short):
	tc, roots = ORDER_2026[short]
	total = tc + roots
	return round(tc * 100.0 / total, 2), round(roots * 100.0 / total, 2)


def main(apply=0):
	apply = cint(apply)
	rows = frappe.get_all("Crop Protocol", filters={"custom_is_summer_flower": 1},
	                      fields=["name", "variety", "farm",
	                              "custom_sf_protocol_status"],
	                      limit_page_length=0)
	mine = [r for r in rows if _short(r.variety) and (
		"eryng" in (r.variety or "").lower() or "artemis" in (r.variety or "").lower())]
	print("%d Eryngium protocols to set up\n" % len(mine))

	done, skipped = 0, []
	for r in sorted(mine, key=lambda x: x.name):
		short = _short(r.variety)
		tc_pct, root_pct = shares(short)
		print("%-46s %-14s  TC %5.2f%% / Roots %5.2f%%"
		      % (r.name[:46], r.custom_sf_protocol_status, tc_pct, root_pct))
		if not apply:
			continue
		doc = frappe.get_doc("Crop Protocol", r.name)
		doc.set("custom_sf_material_route", [])
		for stage, weeks, buy, lead, share in (
			("TC", 0, 1, TC_LEAD, tc_pct),
			("Propagation", TC_HARDEN, 0, 0, None),
			("Plants", 0, 0, 0, None),
			("Roots", 0, 1, ROOT_LEAD, root_pct),
			("Propagation", ROOT_HARDEN, 0, 0, None),
			("Plants", 0, 0, 0, None),
		):
			doc.append("custom_sf_material_route", {
				"row_type": "Stage", "stage": stage, "weeks": weeks,
				"is_purchase": buy, "lead_weeks": lead, "share_pct": share,
			})
		# The supplier lead on the protocol itself is the tissue culture one, which
		# is what the majority of the order is bought on; each route carries its own
		# on the row, and that is what the order date is worked back from.
		doc.custom_sf_supplier_lead_weeks = TC_LEAD
		if not cint(doc.get("custom_sf_sticking_to_planting_weeks")):
			doc.custom_sf_sticking_to_planting_weeks = TC_HARDEN
		doc.flags.ignore_permissions = True
		try:
			doc.save()
			done += 1
		except Exception as e:
			skipped.append((r.name, str(e)[:90]))

	if not apply:
		print("\n   (dry run -- pass apply=1)")
		return
	frappe.db.commit()
	print("\n   %d protocols written" % done)
	for name, why in skipped:
		print("   REFUSED %-42s %s" % (name[:42], why))
