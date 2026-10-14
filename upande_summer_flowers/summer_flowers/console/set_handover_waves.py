# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Put the lift profile on Summer Flower Settings.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_handover_waves.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_handover_waves.main \\
		--kwargs "{'apply': 1, 'force': 1}"

A consignment is not handed over in one go. It is lifted in waves, and each
wave is a share of it -- which is the whole arithmetic of when plants reach the
farm, because a lift that is 25% of 75,000 plants is 18,750 of them arriving
four weeks after the first.

Roots are the farm's stated protocol; tissue culture is measured from the
Eryngium delivery book. The shares of each profile must add to a hundred and
the settings now refuse a set that does not.

Rows already on the settings are left alone unless `force` is given, because
somebody may have corrected them from a later season.
"""

import frappe
from frappe.utils import cint

# (supplier, form, lift, weeks after it arrives, share %)
#
# ROOTS are the farm's own stated protocol, not a measurement. A root goes to
# the trough, and each lift comes off after its own time there plus seven weeks
# rooting:
#
#     lift 1   8 weeks trough + 7 rooting = 15   45%
#     lift 2  10              + 7         = 17   30%
#     lift 3  12              + 7         = 19   25%
#
# So 5,000 roots at fifteen plants each -- 75,000 plants -- hand over 33,750
# then 22,500 then 18,750. The delivery book only ever recorded two grades, so
# measuring it gave two lifts at 77/23; the protocol has three and the protocol
# is what the unit works to.
#
# TISSUE CULTURE is measured, from the consignments in that book whose own
# arithmetic closes.
WAVES = [
	(None, "Roots", 1, 15, 45.0),
	(None, "Roots", 2, 17, 30.0),
	(None, "Roots", 3, 19, 25.0),
	(None, "Tissue Culture", 1, 7, 87.5),
	(None, "Tissue Culture", 2, 8, 12.5),
	("Iribov", "Tissue Culture", 1, 7, 88.3),
	("Iribov", "Tissue Culture", 2, 8, 11.7),
	("Stokman", "Tissue Culture", 1, 8, 84.9),
	("Stokman", "Tissue Culture", 2, 9, 15.1),
]


def main(apply=0, force=0):
	apply, force = cint(apply), cint(force)
	s = frappe.get_single("Summer Flower Settings")
	have = s.get("handover_waves") or []
	print(f"Summer Flower Settings carries {len(have)} lift rows")
	for r in have:
		print(f"  {r.source or '(any supplier)':<16} {r.form:<16} lift {r.lift_no}"
		      f"  {r.weeks_after_arrival} wk  {r.share_pct}%")
	if have and not force:
		print("  leaving them alone — pass force=1 to replace")
		return
	print("\nwould write" if not apply else "\nwriting")
	for src, form, lift, weeks, share in WAVES:
		known = bool(src) and frappe.db.exists("Supplier", src)
		note = "" if (not src or known) else "   (no such supplier here — skipped)"
		print(f"  {src or '(any supplier)':<16} {form:<16} lift {lift}"
		      f"  {weeks} wk  {share}%{note}")
	if not apply:
		print("\n(dry run — nothing written)")
		return
	s.set("handover_waves", [])
	for src, form, lift, weeks, share in WAVES:
		if src and not frappe.db.exists("Supplier", src):
			continue
		s.append("handover_waves", {
			"source": src, "form": form, "lift_no": lift,
			"weeks_after_arrival": weeks, "share_pct": share,
		})
	s.flags.ignore_permissions = True
	s.save(ignore_permissions=True)
	frappe.db.commit()
	print(f"\nwritten: {len(s.handover_waves)} rows")
