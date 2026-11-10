# Copyright (c) 2026, James Kiruga and contributors
"""What a production plan asks of the land, block by block."""

import frappe
from frappe import _

from upande_summer_flowers.summer_flowers import plan_space


def execute(filters=None):
	filters = filters or {}
	plan = filters.get("production_plan")
	if not plan:
		return columns(), [], _("Pick a production plan.")

	a = plan_space.analyse(plan)
	t = a["totals"]
	rows = a["blocks"]
	message = "<b>{0}</b><br>{1}".format(
		frappe.utils.escape_html(a["ground_demanded"] or ""),
		frappe.utils.escape_html(a["verdict"] or "").replace("\n", "<br>"))
	if rows:
		rows.append({
			"block": _("All blocks"),
			"beds_stated": t["beds_available_stated"],
			"beds_measured": t["beds_available_measured"],
			"area_stated_ha": t["space_stated_ha"],
			"area_measured_ha": t["space_measured_ha"],
			"beds_needed_by_plan": t["beds_required_peak"],
			"beds_held_at_start": t.get("beds_held_at_start", 0),
			"beds_held_by_others": t.get("beds_held_peak", 0),
			"verdict": _("{0}% of the farm's stated space, at the peak").format(
				round(t["space_utilisation_pct"], 1)),
		})
	return columns(), rows, message


def columns():
	return [
		{"fieldname": "block", "label": _("Block"), "fieldtype": "Link",
		 "options": "Block", "width": 190},
		{"fieldname": "beds_stated", "label": _("Beds Stated"),
		 "fieldtype": "Int", "width": 100},
		{"fieldname": "beds_measured", "label": _("Beds Measured"),
		 "fieldtype": "Int", "width": 115},
		{"fieldname": "area_stated_ha", "label": _("Area Stated (ha)"),
		 "fieldtype": "Float", "precision": 3, "width": 130},
		{"fieldname": "area_measured_ha", "label": _("Area Measured (ha)"),
		 "fieldtype": "Float", "precision": 3, "width": 145},
		{"fieldname": "beds_needed_by_plan", "label": _("Beds This Plan Needs"),
		 "fieldtype": "Int", "width": 160},
		{"fieldname": "beds_held_at_start", "label": _("Held In Week 1"),
		 "fieldtype": "Int", "width": 125},
		{"fieldname": "beds_held_by_others", "label": _("Held At The Peak"),
		 "fieldtype": "Int", "width": 140},
		{"fieldname": "frees_on", "label": _("Last Crop Out"),
		 "fieldtype": "Date", "width": 125},
		{"fieldname": "beds_free", "label": _("Beds Free"),
		 "fieldtype": "Int", "width": 100},
		{"fieldname": "verdict", "label": _("Verdict"), "fieldtype": "Data",
		 "width": 240},
	]
