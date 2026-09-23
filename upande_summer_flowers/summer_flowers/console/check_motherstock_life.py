"""What multiplying costs in cutting weeks, now the clock starts at generation one."""

import frappe
from frappe.utils import add_days, cint, getdate


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	name = frappe.db.get_value("Summer Flower Motherstock Batch",
	                           {"docstatus": ["<", 2]}, "name")
	if not name:
		print("no motherstock batch on this site")
		return
	b = frappe.get_doc("Summer Flower Motherstock Batch", name)
	p = frappe.get_cached_doc("Crop Protocol Version", b.protocol)
	est = p.weeks_tc_to_first_cut()
	life = cint(p.motherstock_life_weeks)
	print("batch    %s -- %s at %s" % (b.name, b.variety, b.farm))
	print("protocol %s: %s weeks TC to first cut, line lives %s weeks"
	      % (p.name, est, life))
	print("\n%-8s %-10s %-13s %-13s %-13s %-9s %s"
	      % ("cycles", "mothers", "TC to order", "gen 1 cuts", "pool full",
	         "expires", "cutting weeks"))
	for c in range(1, 7):
		b.build_up_cycles = c
		b.override_tc_plants = 0
		b.override_tc_order_date = 0
		b.flags.ignore_permissions = True
		b.save()
		print("%-8s %-10s %-13s %-13s %-13s %-9s %s"
		      % (c, f"{cint(b.mother_plants):,}", f"{cint(b.tc_plants_required):,}",
		         b.line_start_date, b.first_sticking_date, b.expiry_date,
		         b.productive_weeks))
	print("\nwhat it warns:")
	b.build_up_cycles = 4
	b.save()
	print("  " + (b.schedule_warning or "-").replace("\n", "\n  "))
