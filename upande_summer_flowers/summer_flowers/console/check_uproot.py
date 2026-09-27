"""Uproot a planting to free its block, and read the uprooting plan. Rolls back."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback()[-800:])
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			uproot_to_free, uprooting_plan,
		)

	d = frappe.db.get_value("Planting Calendar",
	                        {"calendar_status": ["in", ("Approved", "Planted")],
	                         "block": ["is", "set"]},
	                        ["name", "block", "variety", "planting_date",
	                         "planned_uproot_date", "beds"], as_dict=True)
	print("%s on %s — planted %s, due out %s"
	      % (d.name, d.block, d.planting_date, d.planned_uproot_date))

	from frappe.utils import add_days
	early = add_days(d.planned_uproot_date, -90)
	print("\nuprooting it 90 days early (%s):" % early)
	out = uproot_to_free(d.name, early)
	print("   %s weeks early, %s flushes lost, %s stems given up"
	      % (out["weeks_early"], out["flushes_lost"], f"{out['stems_lost']:,}"))

	print("\nand it cannot be pushed later:")
	try:
		uproot_to_free(d.name, add_days(d.planned_uproot_date, 30))
		print("   !! allowed")
	except frappe.ValidationError as e:
		print("   refused: %s" % frappe.utils.strip_html(str(e))[:90])

	p = uprooting_plan(farm=frappe.db.get_value("Planting Calendar", d.name, "farm"))
	print("\nuprooting plan: %s plantings, %s beds freed, %s stems forgone"
	      % (len(p["rows"]), p["beds_freed"], f"{p['stems_forgone']:,}"))
	for r in p["rows"][:4]:
		print("   %-16s %-26s out %s (%s wks early), %s stems forgone"
		      % (r["name"], (r["block"] or "")[:26], r["actual_uproot_date"],
		         r["weeks_early"], f"{cint(r['stems_forgone']):,}"))
