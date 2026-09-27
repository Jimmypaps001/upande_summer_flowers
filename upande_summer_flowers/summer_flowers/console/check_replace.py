"""Replacing a procurement plan, and being told when you cannot. Rolls back."""

import frappe
from frappe.utils import add_days, cint, nowdate


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback()[-900:])
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			build, existing_for, methods_for,
		)

	plan = frappe.db.get_value("Summer Flower Production Plan",
	                           {"variety": "Aster Pink Flash", "farm": "Karen",
	                            "docstatus": 1}, "name", order_by="creation desc")
	for n in frappe.get_all("Summer Flower Procurement Plan",
	                        filters={"production_plan": plan, "docstatus": ["<", 2]},
	                        pluck="name"):
		d = frappe.get_doc("Summer Flower Procurement Plan", n)
		if d.docstatus == 1:
			d.flags.ignore_permissions = True
			d.cancel()
		frappe.delete_doc("Summer Flower Procurement Plan", n, force=True,
		                  ignore_permissions=True)

	print("nothing sourcing it yet: %s" % existing_for(plan))
	first = build(plan)
	print("built %s" % first)
	have = existing_for(plan)
	print("\nnow the dialog sees: %s (draft) can_replace=%s"
	      % (have["name"], have["can_replace"]))

	print("\nGenerate without asking to replace:")
	try:
		build(plan)
		print("   !! allowed a second plan")
	except frappe.ValidationError as e:
		print("   refused: %s" % frappe.utils.strip_html(str(e))[:100])

	print("\nGenerate with replace=1:")
	second = build(plan, replace=1)
	print("   built %s, and %s is gone: %s"
	      % (second, first, not frappe.db.exists("Summer Flower Procurement Plan",
	                                             first)))

	print("\nnow approve it and try again:")
	q = frappe.get_doc("Summer Flower Procurement Plan", second)
	q.flags.ignore_permissions = True
	q.submit()
	have = existing_for(plan)
	print("   can_replace=%s" % have["can_replace"])
	print("   why not: %s" % (have["why_not"] or "-")[:170])
	try:
		build(plan, replace=1)
		print("   !! replaced an approved plan")
	except frappe.ValidationError as e:
		print("   refused: %s" % frappe.utils.strip_html(str(e))[:150])
