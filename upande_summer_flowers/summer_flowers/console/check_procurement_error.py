"""What actually happens when you press Generate on a routeless protocol."""

import frappe


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()


def _run():
	from upande_summer_flowers.summer_flowers import planning_api
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			build, methods_for,
		)

	plan = planning_api.resolve_plan()
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	print("plan %s — %s at %s, protocol %s" % (plan, p.variety, p.farm, p.protocol))
	n = frappe.db.count("Crop Material Stage",
	                    {"parent": p.protocol, "parenttype": "Crop Protocol Version"})
	print("route rows on that version: %s" % n)

	print("\n--- methods_for() ---")
	try:
		m = methods_for(plan)
		print("   has_route  %s" % m["has_route"])
		print("   decided    %s" % m["decided"])
		print("   options    %s" % [o["stage"] for o in m["options"]])
	except Exception as e:
		print("   RAISED %s: %s" % (type(e).__name__, str(e)[:200]))

	print("\n--- build(), exactly as the dialog calls it ---")
	for old in frappe.get_all("Summer Flower Procurement Plan",
	                          filters={"production_plan": plan, "docstatus": 0},
	                          pluck="name"):
		frappe.delete_doc("Summer Flower Procurement Plan", old, force=True,
		                  ignore_permissions=True)
	try:
		out = build(production_plan=plan, method="Purchase", entry_stage=None,
		            supplier=None, fit_to_space=1)
		print("   built %s" % out)
	except Exception as e:
		print("   RAISED %s" % type(e).__name__)
		print("   message: %s" % str(e)[:400])
		tb = frappe.get_traceback()
		if "Traceback" in tb:
			print("   --- traceback tail ---")
			print("\n".join(tb.strip().splitlines()[-6:]))
