# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Where plants are raised, as warehouses, and what each one can take.

A propagation unit holds stock, so it is a warehouse. Capacity belongs on it
rather than in one global figure, because the units are not alike: Kudenga holds
756,000 and takes both forms, Bondet holds 192,500 and has only ever taken
roots, and Plantech is an outside propagator that has never stated a figure.

Only the fields are created here. The figures are a site's own, and putting them
in an app would be asserting that every farm's benches are the same size.

Two kinds of number are kept apart on purpose. A stated limit is what somebody
says the unit holds. The biggest week on record is what it has actually taken
in -- evidence of what it has done, not a promise of what it can do -- so it is
a separate field, and the planner only falls back to it when nobody has stated
a limit, and says when it has.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELDS = {
	"Warehouse": [
		{"fieldname": "custom_sf_propagation_section", "fieldtype": "Section Break",
		 "label": "Propagation Capacity", "insert_after": "disabled",
		 "collapsible": 1},
		{"fieldname": "custom_sf_is_propagation_unit", "fieldtype": "Check",
		 "label": "Propagation Unit",
		 "insert_after": "custom_sf_propagation_section",
		 "description": "Tick where plants are raised. The summer flower planner "
		                "measures a delivery programme against every unit ticked "
		                "here, taken together, because a farm's material goes "
		                "through more than one of them."},
		{"fieldname": "custom_sf_takes_tc", "fieldtype": "Check",
		 "label": "Takes Tissue Culture", "default": "1",
		 "insert_after": "custom_sf_is_propagation_unit",
		 "depends_on": "custom_sf_is_propagation_unit"},
		{"fieldname": "custom_sf_takes_roots", "fieldtype": "Check",
		 "label": "Takes Roots", "default": "1",
		 "insert_after": "custom_sf_takes_tc",
		 "depends_on": "custom_sf_is_propagation_unit"},
		{"fieldname": "custom_sf_capacity_plants", "fieldtype": "Int",
		 "label": "Plants It Can Hold", "insert_after": "custom_sf_takes_roots",
		 "depends_on": "custom_sf_is_propagation_unit",
		 "description": "The most that can be in propagation here at one moment, "
		                "whatever form it is in. Zero means nobody has stated it "
		                "and no ceiling is applied."},
		{"fieldname": "custom_sf_tc_capacity_plants", "fieldtype": "Int",
		 "label": "Of Which Tissue Culture",
		 "insert_after": "custom_sf_capacity_plants",
		 "depends_on": "custom_sf_takes_tc"},
		{"fieldname": "custom_sf_roots_capacity_plants", "fieldtype": "Int",
		 "label": "Of Which Roots",
		 "insert_after": "custom_sf_tc_capacity_plants",
		 "depends_on": "custom_sf_takes_roots"},
		{"fieldname": "custom_sf_cb_propagation", "fieldtype": "Column Break",
		 "insert_after": "custom_sf_roots_capacity_plants"},
		{"fieldname": "custom_sf_weekly_intake_plants", "fieldtype": "Int",
		 "label": "Most It Can Take In A Week",
		 "insert_after": "custom_sf_cb_propagation",
		 "depends_on": "custom_sf_is_propagation_unit",
		 "description": "A stated limit on one week's intake. Zero falls back to "
		                "the biggest week on record, because a programme measured "
		                "against nothing is not measured."},
		{"fieldname": "custom_sf_observed_peak_intake", "fieldtype": "Int",
		 "label": "Biggest Week On Record",
		 "insert_after": "custom_sf_weekly_intake_plants",
		 "depends_on": "custom_sf_is_propagation_unit",
		 "description": "What this unit has actually taken in, in its heaviest "
		                "week. Evidence of what it has done, not a promise of "
		                "what it can do."},
		{"fieldname": "custom_sf_hold_weeks_tc", "fieldtype": "Int",
		 "label": "Weeks Tissue Culture Occupies It",
		 "insert_after": "custom_sf_observed_peak_intake",
		 "depends_on": "custom_sf_takes_tc",
		 "description": "The bench is not clear when the first lift is ready. The "
		                "planner holds the space until the last lift comes off, "
		                "so a shorter figure here does not shorten it."},
		{"fieldname": "custom_sf_hold_weeks_roots", "fieldtype": "Int",
		 "label": "Weeks Roots Occupy It",
		 "insert_after": "custom_sf_hold_weeks_tc",
		 "depends_on": "custom_sf_takes_roots"},
	],
}


def install():
	"""Idempotent: creates what is missing and leaves any figures alone."""
	if not frappe.db.table_exists("Warehouse"):
		return
	create_custom_fields(FIELDS, update=True)
