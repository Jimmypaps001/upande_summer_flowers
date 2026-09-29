"""Generating twice revises the same plan instead of destroying it. Rolled back."""

import frappe
from frappe.utils import add_days, cint, nowdate


def main(plan="SFPP-2026-00061"):
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			build, existing_for,
		)

	before = frappe.get_all("Summer Flower Procurement Plan",
	                        filters={"production_plan": plan},
	                        fields=["name", "docstatus"], limit_page_length=0)
	print("plan %s: %d procurement plan(s) to start with" % (plan, len(before)))
	have = existing_for(plan)
	print("   current: %s docstatus=%s order_by=%s passed=%s"
	      % ((have or {}).get("name"), (have or {}).get("docstatus"),
	         (have or {}).get("first_order_by"), (have or {}).get("order_date_passed")))

	# A submitted plan whose order date has NOT come round should be revised, not
	# refused and not deleted.
	if have and have["docstatus"] == 1 and have["order_date_passed"]:
		frappe.db.set_value("Summer Flower Procurement Plan", have["name"],
		                    "first_order_by", add_days(nowdate(), 400))
		have = existing_for(plan)
		print("   (moved its order date into the future for this test)")

	name = build(plan, replace=1, divert_weeks=4)
	after = frappe.get_all("Summer Flower Procurement Plan",
	                       filters={"production_plan": plan},
	                       fields=["name", "docstatus", "amended_from"],
	                       limit_page_length=0)
	print("\n   build returned %s" % name)
	print("   now %d procurement plan(s):" % len(after))
	for r in after:
		print("      %-22s docstatus=%s amended_from=%s"
		      % (r.name, r.docstatus, r.amended_from or "-"))

	kept = {r.name for r in before} & {r.name for r in after}
	print("\n   original document still exists: %s"
	      % ("yes" if kept else "NO -- IT WAS DELETED"))
	again = build(plan, replace=1, divert_weeks=2)
	print("   generating a second time returned %s" % again)
	third = frappe.get_all("Summer Flower Procurement Plan",
	                       filters={"production_plan": plan}, pluck="name")
	print("   total procurement plans for this production plan: %d" % len(third))
	frappe.db.rollback()
	print("\n   (rolled back -- nothing kept)")
