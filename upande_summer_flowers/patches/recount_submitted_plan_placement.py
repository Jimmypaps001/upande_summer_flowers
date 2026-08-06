"""Recount the placement totals on submitted plans built under the old rule.

Until the planner stopped waiting for a block, a proposed planting with no block was
excluded from beds required, plants required and the peak sticking week. Two approved
plans were built that way and store zero plants against 750,000 and 544,000 in their
own rows -- and a submitted document cannot be re-saved to fix it.

These are read-only derived summaries, not accounting entries, so they are corrected in
place. Nothing about the plan's approval, its budget or its plantings changes.
"""

import frappe
from frappe.utils import cint


def execute():
	if not frappe.db.has_column("Summer Flower Production Plan", "unmet_stems"):
		return
	for name in frappe.get_all("Summer Flower Production Plan",
	                           filters={"docstatus": 1}, pluck="name"):
		rows = frappe.get_all("Summer Flower Plan Block",
		                      filters={"parent": name, "is_new_planting": 1},
		                      fields=["beds", "plants", "block", "sticking_year",
		                              "sticking_week"])
		if not rows:
			continue
		beds = sum(cint(r.beds) for r in rows)
		plants = sum(cint(r.plants) for r in rows)
		awaiting = len([r for r in rows if not r.block])
		peak, peak_week = 0, None
		by_week = {}
		for r in rows:
			if not (r.sticking_year and r.sticking_week):
				continue
			key = (cint(r.sticking_year), cint(r.sticking_week))
			by_week[key] = by_week.get(key, 0) + cint(r.plants)
		if by_week:
			key = max(by_week, key=lambda k: by_week[k])
			peak, peak_week = by_week[key], "%s-W%02d" % key
		current = frappe.db.get_value("Summer Flower Production Plan", name,
		                              "new_plants_required")
		if cint(current) == plants:
			continue
		frappe.db.set_value("Summer Flower Production Plan", name, {
			"new_beds_required": beds,
			"new_plants_required": plants,
			"plantings_not_placed": awaiting,
			"peak_weekly_sticking_planned": peak,
			"peak_sticking_week_planned": peak_week,
		}, update_modified=False)
		frappe.logger().info(
			"recount_submitted_plan_placement: %s plants %s -> %s, awaiting %s"
			% (name, cint(current), plants, awaiting))
