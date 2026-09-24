"""Every lever on the TC dialog, followed through to the document it writes."""

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
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			build, tc_options,
		)

	plan = frappe.db.get_value("Summer Flower Production Plan",
	                           {"variety": "Aster Pink Flash", "farm": "Karen",
	                            "docstatus": 1}, "name", order_by="creation desc")
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	print("plan %s — peak %s plants in %s, season total %s plants"
	      % (plan, f"{cint(p.peak_weekly_sticking_planned):,}",
	         p.peak_sticking_week_planned, f"{cint(p.new_plants_required):,}"))
	print("plan's own TC block: %s plantlets, order by %s\n"
	      % (f"{cint(p.tc_plants_to_order):,}", p.tc_order_by_date))

	def wipe():
		for n in frappe.get_all("Summer Flower Procurement Plan",
		                        filters={"production_plan": plan,
		                                 "docstatus": ["<", 2]}, pluck="name"):
			dd = frappe.get_doc("Summer Flower Procurement Plan", n)
			if dd.docstatus == 1:
				dd.flags.ignore_permissions = True
				dd.cancel()
			frappe.delete_doc("Summer Flower Procurement Plan", n, force=True,
			                  ignore_permissions=True)

	print("%-28s %-10s %-10s %-12s %-8s %s"
	      % ("choice", "TC line", "sum says", "order by", "cycles", "overridden"))
	cases = [
		("default (protocol)", {}),
		("cycles 1", {"cycles": 1}),
		("cycles 2", {"cycles": 2}),
		("cycles 5", {"cycles": 5}),
		("cycles 4, buy 5,000", {"cycles": 4, "tc_qty": 5000}),
		("cycles 1, buy 60,000", {"cycles": 1, "tc_qty": 60000}),
	]
	for label, args in cases:
		wipe()
		q = frappe.get_doc("Summer Flower Procurement Plan", build(plan, **args))
		est = next((r for r in q.requirements
		            if (r.line_type or "") == "Establishment"), None)
		print("%-28s %-10s %-10s %-12s %-8s %s"
		      % (label, f"{cint(est.qty_to_order):,}" if est else "-",
		         f"{cint(q.calculated_units):,}", est.order_by_date if est else "-",
		         q.multiplication_cycles, "yes" if q.units_overridden else ""))

	wipe()
	print("\nthe establishment line is one purchase, the cohorts draw on it:")
	q = frappe.get_doc("Summer Flower Procurement Plan", build(plan))
	kinds = {}
	for r in q.requirements:
		kinds[r.line_type or "Planting"] = kinds.get(r.line_type or "Planting", 0) + 1
	print("   lines %s · total units %s · plants at field %s"
	      % (kinds, f"{cint(q.total_units_to_order):,}",
	         f"{cint(q.total_plants_at_field):,}"))
	drawn = sum(1 for r in q.requirements if cint(r.drawn_from_pool))
	print("   %s cohort lines marked as cut from the motherstock, %s buying anything"
	      % (drawn, sum(1 for r in q.requirements
	                    if (r.line_type or "") == "Planting" and cint(r.qty_to_order))))

	print("\na route with no pool has nothing to choose:")
	for name in frappe.get_all("Summer Flower Production Plan",
	                           filters={"docstatus": 1}, pluck="name", limit=40):
		o = tc_options(name)
		if not o.get("standing"):
			print("   %s -> standing=False (%s)"
			      % (name, (o.get("reason") or "")[:70]))
			break
