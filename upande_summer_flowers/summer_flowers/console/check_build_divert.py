"""Build a procurement plan through the solver, then roll it back."""

import frappe
from frappe.utils import cint


def main(plan=None):
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import build
	from upande_summer_flowers.summer_flowers import propagation_solver as psol

	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			existing_for,
		)
	if not plan:
		for row in frappe.get_all("Summer Flower Production Plan",
		                          filters={"docstatus": ["<", 2]},
		                          fields=["name"], order_by="modified desc",
		                          limit_page_length=60):
			d = frappe.get_doc("Summer Flower Production Plan", row.name)
			if not d.get("protocol"):
				continue
			ex = existing_for(row.name)
			if ex and ex.get("name") and not ex.get("can_replace"):
				continue
			if psol.recommend(d).get("solvable"):
				plan = row.name
				break
	if not plan:
		print("no solvable plan on this site")
		return
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	print("plan %s -- %s at %s" % (plan, p.variety, p.farm))

	ex = existing_for(plan)
	replace = 1 if ex and ex.get("name") else 0
	print("existing procurement plan: %s (replace=%s)"
	      % ((ex or {}).get("name") or "none", replace))

	for divert in (None, 0, 2, 4):
		frappe.db.rollback()
		try:
			name = build(plan, replace=replace, divert_weeks=divert)
		except Exception as e:
			print("  divert=%-4s  THROWN: %s" % (divert, str(e)[:110]))
			continue
		doc = frappe.get_doc("Summer Flower Procurement Plan", name)
		est = [r for r in doc.requirements if r.line_type == "Establishment"]
		print("  divert=%-4s  %s  units=%s  pool=%s  divert_weeks=%s  order_by=%s"
		      % (divert, name, f"{cint(est[0].qty_to_order):,}" if est else "-",
		         f"{cint(doc.pool_plants):,}", cint(doc.divert_weeks),
		         est[0].order_by_date if est else "-"))
		print("      basis: %s" % (doc.sizing_basis or "")[:150])
	frappe.db.rollback()
	print("\n  (rolled back -- nothing kept)")
