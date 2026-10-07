# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Amend an approved production plan so it can be rebuilt.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.amend_plan.main \\
		--kwargs "{'plan': 'SFPP-2026-00067'}"
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.amend_plan.main \\
		--kwargs "{'plan': 'SFPP-2026-00067', 'apply': 1}"

An approved plan cannot be regenerated: that is the point of approving it. To
put a rebuilt planting programme behind an approved season the plan has to be
cancelled and amended, which is a new document with a new number.

What it does NOT do is approve the amendment. Approval raises a budget, and
raising a financial document is not a side effect of rebuilding arithmetic.

The workflow has no transition out of Approved, so the cancel is direct and the
workflow state is set to match it afterwards -- otherwise the cancelled document
goes on claiming to be approved.
"""

import frappe
from frappe.utils import cint


def _summary(doc):
	rows = [b for b in doc.plan_blocks if cint(b.is_new_planting)]
	demand = sum(cint(w.demand_stems) for w in doc.plan_weeks)
	grown = sum(cint(w.production_stems) for w in doc.plan_weeks)
	band = (doc.get("weekly_variance_band_pct") or 0) / 100.0
	outside = sum(1 for w in doc.plan_weeks
	              if cint(w.demand_stems) and band and
	              abs(cint(w.production_stems) - cint(w.demand_stems))
	              > cint(w.demand_stems) * band + 1)
	return (f"{len(rows)} plantings, {sum(cint(b.plants) for b in rows):,} plants, "
	        f"{grown:,} of {demand:,} stems"
	        f"{f' = {grown / demand * 100:.1f}%' if demand else ''}"
	        + (f", {outside} weeks outside the band" if band else ""))


def main(plan=None, apply=0):
	apply = cint(apply)
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	print(f"{doc.name}  {doc.variety} at {doc.farm}")
	print(f"  state        docstatus {doc.docstatus}, {doc.status}, "
	      f"workflow {doc.get('workflow_state')}")
	print(f"  now          {_summary(doc)}")
	print(f"  budget       {doc.budget or 'none'}")
	for dt, field in (("Summer Flower Procurement Plan", "production_plan"),):
		for r in frappe.get_all(dt, filters={field: doc.name},
		                        fields=["name", "docstatus"]):
			print(f"  {dt}  {r.name} (docstatus {r.docstatus}) — stays on the old figures")
	if doc.docstatus != 1:
		print("  not submitted: regenerate it directly instead of amending")
		return
	if not apply:
		print("  would cancel this plan, amend it, and rebuild the amendment")
		print("  (dry run — nothing done)")
		return

	doc.cancel()
	# The workflow stops at Approved and offers no way back, so the state has to
	# be put right by hand or a cancelled plan goes on reading as approved.
	doc.db_set("workflow_state", "Rejected", update_modified=False)
	frappe.db.commit()
	print(f"  cancelled    docstatus {doc.docstatus}")

	new = frappe.copy_doc(doc)
	new.amended_from = doc.name
	new.docstatus = 0
	new.workflow_state = "Draft"
	new.status = "Draft"
	new.insert()
	frappe.db.commit()
	print(f"  amended to   {new.name}")

	new.regenerate()
	frappe.db.commit()
	print(f"  rebuilt      {_summary(new)}")
	print(f"  left in      {new.docstatus} / {new.get('workflow_state')} — "
	      f"approve it yourself; that is what raises the budget")
	return new.name
