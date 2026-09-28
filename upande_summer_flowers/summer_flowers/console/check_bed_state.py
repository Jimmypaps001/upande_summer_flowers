"""Beds are reserved until the planting says it went in. Rolls back."""

import frappe
from frappe.utils import nowdate


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback()[-700:])
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	name = frappe.db.get_value("Planting Calendar",
	                           {"actual_planting_date": ["is", "not set"],
	                            "block": ["is", "set"]}, "name")
	d = frappe.get_doc("Planting Calendar", name)
	print("%s — %s beds allocated, actual planting %s"
	      % (d.name, len(d.bed_allocation), d.actual_planting_date or "not recorded"))
	print("   bed rows say: %s"
	      % sorted({r.bed_status for r in d.bed_allocation}))
	beds = [r.bed for r in d.bed_allocation if r.bed][:3]
	print("   Bed records say: %s"
	      % [frappe.db.get_value("Bed", b, "custom_bed_status") for b in beds])

	print("\nrecording that it was planted today:")
	d.actual_planting_date = nowdate()
	d.flags.ignore_permissions = True
	d.flags.built_in_bulk = True
	d.save()
	d.reload()
	print("   calendar status: %s" % d.calendar_status)
	print("   bed rows say: %s" % sorted({r.bed_status for r in d.bed_allocation}))
	print("   Bed records say: %s"
	      % [frappe.db.get_value("Bed", b, "custom_bed_status") for b in beds])
