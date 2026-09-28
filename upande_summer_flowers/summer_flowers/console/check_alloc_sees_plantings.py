"""Does the picker know a block already holds a planting? Rolls back."""

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
			allocation_options,
		)

	plan = frappe.db.get_value("Summer Flower Production Plan",
	                           {"variety": "Aster Pink Flash", "farm": "Karen",
	                            "docstatus": 1}, "name", order_by="creation desc")
	s = allocation_options(plan)
	print("plan %s — %d rows to allocate" % (plan, len(s["rows"])))

	# What is actually standing at this farm, by block
	held = {}
	for r in frappe.get_all("Planting Calendar",
	                        filters={"farm": "Karen",
	                                 "calendar_status": ["in", ("Draft",
	                                                            "Pending Approval",
	                                                            "Approved", "Planted")]},
	                        fields=["name", "block", "beds", "variety",
	                                "planting_date", "planned_uproot_date"]):
		held.setdefault(r.block, []).append(r)
	print("blocks with a planting on them: %d" % len(held))

	shown = 0
	for row in s["rows"]:
		for c in row["candidates"]:
			on_it = held.get(c["block"]) or []
			if not on_it:
				continue
			total = cint(frappe.db.get_value("Block", c["block"], "custom_total_beds"))
			print("\n%s wants %s beds" % (row["planting_week"], row["beds"]))
			print("   %s: total %s, picker says free %s, fits=%s"
			      % (c["block"].split(" - ")[-1], total, c["free_beds"], c["fits"]))
			print("   really standing there: %s"
			      % ", ".join("%s %s beds to %s" % (x.name, cint(x.beds),
			                                        x.planned_uproot_date)
			                  for x in on_it[:3]))
			print("   picker now reports standing: %s"
			      % [(b["variety"], b["beds"], b["frees_on"])
			         for b in (c.get("standing") or [])][:3])
			print("   and the block's history: %s"
			      % [(h["variety"], h["planted"], h["status"])
			         for h in (c.get("history") or [])][:3])
			shown += 1
			break
		if shown >= 3:
			break
	if not shown:
		print("\nno candidate block currently holds a planting")
