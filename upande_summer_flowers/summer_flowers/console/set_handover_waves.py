# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Put the measured lift profile on Summer Flower Settings.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_handover_waves.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_handover_waves.main \\
		--kwargs "{'apply': 1}"

The figures are what the Eryngium delivery book records across 2025 and 2026,
not a guess: for each source, when the first batch of plants came off a
consignment, when the second did, and how the consignment divided between them.

	Iribov   87.7% at  7 weeks, 12.3% at  9      (245 and 112 consignments)
	Stokman  83.1% at  7 weeks, 16.9% at  9      (144 and  52)
	Roots    71.5% at 17 weeks, 28.5% at 20      ( 58 and  32)

The blank-supplier rows are the fallback for a lab nobody has measured, and are
Iribov's, which is the book's largest sample.

Rows already on the settings are left alone unless `force` is given, because
somebody may have corrected them from a later season.
"""

import frappe
from frappe.utils import cint

WAVES = [
	# (supplier, form, lift, weeks after it arrives, share %)
	(None, "Tissue Culture", 1, 7, 87.7),
	(None, "Tissue Culture", 2, 9, 12.3),
	(None, "Roots", 1, 17, 71.5),
	(None, "Roots", 2, 20, 28.5),
	("Iribov", "Tissue Culture", 1, 7, 87.7),
	("Iribov", "Tissue Culture", 2, 9, 12.3),
	("Stokman", "Tissue Culture", 1, 7, 83.1),
	("Stokman", "Tissue Culture", 2, 9, 16.9),
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
