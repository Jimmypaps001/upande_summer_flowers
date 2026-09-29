"""Generating again revises the plan that is there. Rolled back.

The old expectation was the opposite -- a second generate was refused, and
replacing meant deleting -- so this is rewritten rather than repaired.
"""

import frappe
from frappe.utils import add_days, cint, nowdate


def run():
	try:
		_run()
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			build, existing_for,
		)

	plan = frappe.db.get_value("Summer Flower Procurement Plan",
	                           {"docstatus": ["<", 2]}, "production_plan")
	if not plan:
		print("no procurement plan on this site to revise")
		return
	print("production plan %s" % plan)

	have = existing_for(plan)
	print("  starts with %s (docstatus %s), can_revise=%s"
	      % (have["name"], have["docstatus"], have["can_replace"]))

	if have["docstatus"] == 1 and have["order_date_passed"]:
		frappe.db.set_value("Summer Flower Procurement Plan", have["name"],
		                    "first_order_by", add_days(nowdate(), 400))
		have = existing_for(plan)
		print("  (its order date moved into the future for this test)")

	n0 = frappe.db.count("Summer Flower Procurement Plan", {"production_plan": plan})
	first = build(plan, divert_weeks=4)
	n1 = frappe.db.count("Summer Flower Procurement Plan", {"production_plan": plan})
	print("\n  generate once   -> %s   (documents %d -> %d)" % (first, n0, n1))
	print("     the original %s is %s"
	      % (have["name"],
	         {0: "still a draft", 1: "submitted", 2: "cancelled, amended from"}.get(
		         cint(frappe.db.get_value("Summer Flower Procurement Plan",
		                                  have["name"], "docstatus")), "?")))

	second = build(plan, divert_weeks=2)
	n2 = frappe.db.count("Summer Flower Procurement Plan", {"production_plan": plan})
	print("  generate again  -> %s   (documents %d -> %d)" % (second, n1, n2))
	ok_same = second == first
	ok_count = n2 == n1
	print("     same document revised: %s" % ("yes" if ok_same else "NO"))
	print("     no extra document:     %s" % ("yes" if ok_count else "NO"))

	# The one case still refused.
	doc = frappe.get_doc("Summer Flower Procurement Plan", second)
	if doc.docstatus == 0 and doc.requirements:
		doc.flags.ignore_permissions = True
		try:
			doc.submit()
		except Exception as e:
			print("\n  could not submit for the refusal test: %s" % str(e)[:90])
			return
	frappe.db.set_value("Summer Flower Procurement Plan", second,
	                    "first_order_by", add_days(nowdate(), -1))
	after = existing_for(plan)
	print("\n  with its order date in the past: can_revise=%s" % after["can_replace"])
	try:
		build(plan, divert_weeks=4)
		print("     !! it let the order be rewritten anyway")
	except Exception as e:
		print("     refused, correctly: %s" % str(e)[:110])
