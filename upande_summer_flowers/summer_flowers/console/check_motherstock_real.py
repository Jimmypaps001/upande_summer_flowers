"""Is the motherstock the plan is counting on actually there? Rolls back."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	print("every motherstock batch on this site:")
	rows = frappe.get_all("Summer Flower Motherstock Batch",
	                      fields=["name", "variety", "farm", "mother_plants",
	                              "batch_status", "docstatus", "first_sticking_date",
	                              "expiry_date"], limit=0)
	for b in rows:
		print("   %-16s %-22s %-8s %-12s docstatus=%s  %s -> %s"
		      % (b.name, b.variety[:22], f"{cint(b.mother_plants):,}",
		         b.batch_status, b.docstatus, b.first_sticking_date, b.expiry_date))
	if not rows:
		print("   (none)")

	name = frappe.db.get_value("Summer Flower Propagation Plan",
	                           {"variety": "Aster Pink Flash", "docstatus": 0},
	                           "name") or frappe.db.get_value(
	                           "Summer Flower Propagation Plan", {"docstatus": 0},
	                           "name")
	if not name:
		print("\nno draft propagation plan to rebuild")
		return
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	print("\n%s (%s at %s) before rebuild:" % (d.name, d.variety, d.farm))
	print("   cuttings required   %s" % f"{cint(d.total_cuttings_required):,}")
	print("   from existing MS    %s" % f"{cint(d.cuttings_from_existing):,}")
	print("   cover of required   %s%%" % round(d.existing_cover_pct or 0, 1))
	print("   uncovered           %s" % f"{cint(d.cuttings_uncovered):,}")
	print("   mother plants req   %s" % f"{cint(d.mother_plants_required):,}")

	d.flags.ignore_permissions = True
	d.save()
	print("\nafter rebuild, counting only submitted batches:")
	print("   cuttings required   %s" % f"{cint(d.total_cuttings_required):,}")
	print("   from existing MS    %s" % f"{cint(d.cuttings_from_existing):,}")
	print("   cover of required   %s%%" % round(d.existing_cover_pct or 0, 1))
	print("   uncovered           %s" % f"{cint(d.cuttings_uncovered):,}")
	print("   mother plants req   %s" % f"{cint(d.mother_plants_required):,}")
	print("   TC to order         %s" % f"{cint(d.tc_plants_required):,}")
	print("   note: %s" % (d.motherstock_ignored_note or "-"))
	print("\n   sources now:")
	for r in d.sources:
		print("      %-26s %-16s %s mothers, %s/wk, %s -> %s"
		      % (r.source_type, r.motherstock_batch or "-",
		         f"{cint(r.mother_plants):,}", f"{cint(r.weekly_capacity):,}",
		         r.available_from, r.available_to or "no expiry"))
