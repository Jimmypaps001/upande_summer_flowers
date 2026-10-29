# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Yield per hectare for every summer flower protocol.

These figures are worked out on each save of a Crop Protocol and used to sit on
its form. They are read across protocols far more than inside one, so they are
listed here instead.
"""

import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	conditions = {"crop_type": "Summer Flowers"}
	for f in ("farm", "variety"):
		if filters.get(f):
			conditions[f] = filters[f]
	if filters.get("approved_only"):
		conditions["custom_sf_protocol_status"] = "Approved"

	rows = frappe.get_all("Crop Protocol", filters=conditions, order_by="variety, farm", fields=[
		"name as protocol", "variety", "farm", "custom_sf_protocol_status as status",
		"custom_sf_current_version as current_version",
		"custom_sf_plants_per_net_ha as plants_per_net_ha",
		"total_stems_per_plant_life as stems_per_plant_life",
		"custom_sf_stems_per_ha_life as stems_per_ha_life",
		"custom_sf_stems_per_ha_year as stems_per_ha_year",
		"custom_sf_best_year_stems_per_plant as best_year_stems_per_plant",
		"custom_sf_best_year_stems_per_net_ha as best_year_stems_per_net_ha",
		"yield_stems_per_ha as stated_stems_per_ha",
		"custom_sf_yield_variance_pct as variance_pct",
	])
	return columns(), rows


def columns():
	return [
		{"fieldname": "protocol", "label": _("Crop Protocol"), "fieldtype": "Link",
		 "options": "Crop Protocol", "width": 240},
		{"fieldname": "variety", "label": _("Variety"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "farm", "label": _("Farm"), "fieldtype": "Link", "options": "Farm", "width": 130},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "current_version", "label": _("Current Version"), "fieldtype": "Link",
		 "options": "Crop Protocol Version", "width": 200},
		{"fieldname": "plants_per_net_ha", "label": _("Plants per Net Ha"), "fieldtype": "Int", "width": 130},
		{"fieldname": "stems_per_plant_life", "label": _("Stems per Plant (Life)"), "fieldtype": "Float", "width": 140},
		{"fieldname": "stems_per_ha_life", "label": _("Stems per Ha (Life)"), "fieldtype": "Float",
		 "precision": 0, "width": 140},
		{"fieldname": "stems_per_ha_year", "label": _("Stems per Ha (Year)"), "fieldtype": "Float",
		 "precision": 0, "width": 140},
		{"fieldname": "best_year_stems_per_plant", "label": _("Best Year: Stems per Plant"),
		 "fieldtype": "Float", "width": 170},
		{"fieldname": "best_year_stems_per_net_ha", "label": _("Best Year: Stems per Net Ha"),
		 "fieldtype": "Float", "precision": 0, "width": 180},
		{"fieldname": "stated_stems_per_ha", "label": _("Stated Stems per Ha"), "fieldtype": "Int", "width": 150},
		{"fieldname": "variance_pct", "label": _("Computed vs Stated %"), "fieldtype": "Percent", "width": 150},
	]
