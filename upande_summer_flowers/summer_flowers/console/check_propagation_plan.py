# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""What a propagation plan says, against what the material plan says.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_propagation_plan.main \\
		--kwargs "{'plan': 'SFPROP-2026-00014'}"
	... --kwargs "{'plan': 'SFPROP-2026-00014', 'apply': 1}"

Without `apply` the plan is rebuilt in memory and rolled back, so the two can be
compared without one of them being overwritten first.
"""

import frappe
from frappe.utils import cint


def _show(doc, m):
	print(f"  raised from      {doc.raised_from or '(not set)'}")
	print(f"  tc to order      {cint(doc.tc_plants_required):>12,}"
	      f"   material plan says {cint((m.get('asked') or m.get('suggested') or {}).get('tc_qty') or (m.get('suggested') or {}).get('tc')):,}")
	print(f"  plants to raise  {cint(doc.total_plants_to_stick):>12,}"
	      f"   material plan says {cint(m.get('plants_total')):,}")
	print(f"  covered bought   {cint(doc.get('cuttings_from_bought')):>12,}")
	print(f"  uncovered        {cint(doc.cuttings_uncovered):>12,}")
	print(f"  plants short     {cint(doc.plants_short):>12,}")
	print(f"  stems at risk    {cint(doc.stems_at_risk):>12,}")
	print(f"  weeks            {len(doc.weeks):>12}   consignments {len(doc.get('intake') or [])}")
	print(f"  warning          {(doc.schedule_warning or '(none)')[:110]}")


def main(plan=None, apply=0, show=8):
	from upande_summer_flowers.summer_flowers import root_line as rl

	apply = cint(apply)
	doc = frappe.get_doc("Summer Flower Propagation Plan", plan)
	pp = frappe.get_doc("Summer Flower Production Plan", doc.production_plan)
	m = rl.arrival_plan(pp) or {}
	print(f"{doc.name}  {doc.variety} at {doc.farm}  (from {doc.production_plan})")
	print("\nas it stands")
	_show(doc, m)

	doc.run_method("validate")
	print("\nrebuilt")
	_show(doc, m)
	rows = doc.get("intake") or []
	if rows:
		print(f"\n  consignments, first {cint(show)} of {len(rows)}")
		print(f"    {'arrives':<12}{'form':<16}{'from':<28}{'units':>10}"
		      f"{'plants':>10}  {'ready':<12}{'to the farm'}")
		for r in rows[:cint(show)]:
			print(f"    {str(r.week_start_date):<12}{r.form:<16}"
			      f"{(r.source or '')[:26]:<28}{cint(r.units):>10,}"
			      f"{cint(r.plants):>10,}  {str(r.ready_on or ''):<12}"
			      f"{str(r.deliver_on or '')}")
	if apply:
		doc.save()
		frappe.db.commit()
		print("\n  saved")
	else:
		frappe.db.rollback()
		print("\n  (rolled back — nothing kept)")
