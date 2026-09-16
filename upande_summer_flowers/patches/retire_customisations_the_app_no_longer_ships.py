# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Remove the customisations this app used to ship and no longer does.

sync_on_migrate adds and updates; it never deletes. Taking a custom field or a
property setter out of custom/*.json therefore changes nothing on a site that
already has it -- the record stays, the field keeps appearing on the form, and the
app and the sites it is installed on drift apart with nothing in the code saying
so. Everything removed here was removed from the customisation files in the same
work; this is the half of that change a JSON file cannot express.

Nothing here is referenced anywhere in the app: each name was searched for across
the python, json, js and html before being listed.
"""

import frappe

# Summer Flower Market Demand ------------------------------------------------
# VBN is the buyer's own product code. Nothing downstream ever read it, and the
# sheet importer now parses the column without storing it.
DEMAND_FIELDS = ["vbn_code"]

# The base doctype states its own autoname and its own mandatory farm now, so a
# property setter repeating them is a second copy of the same fact.
DEMAND_SETTERS = [
	("Summer Flower Market Demand", None, "autoname"),
	("Summer Flower Market Demand", "farm", "reqd"),
	("Summer Flower Market Demand", "farm", "in_list_view"),
	("Summer Flower Market Demand", "vbn_code", "in_list_view"),
]

# Descriptions moved into the Market Demand walkthrough. Clearing the key in the
# customisation file does not clear it on a record that already carries it: the
# sync writes the keys it is given and leaves the rest alone.
DEMAND_DESCRIPTIONS = ["product_group", "grade_allocation"]

# Crop Protocol --------------------------------------------------------------
# Gross area was dropped when the protocol stopped carrying bed and block
# geometry: a protocol states the variety's characteristics, and how much path
# a farm leaves between beds is the Block's business, not the crop's.
PROTOCOL_SETTERS = [
	# life_expectancy_years is now shipped as a custom field carrying its own label,
	# and being derived rather than mandatory. These setters said the same thing from
	# the outside, and the pair would drift.
	("Crop Protocol", "life_expectancy_years", "label"),
	("Crop Protocol", "life_expectancy_years", "read_only"),
	("Crop Protocol", "life_expectancy_years", "reqd"),
	("Crop Protocol", "life_expectancy_years", "description"),
]

PROTOCOL_FIELDS = [
	"custom_sf_sqm_gross_per_bed",
	"custom_sf_path_allowance_pct",
	"custom_sf_plants_per_gross_ha",
	"custom_sf_stems_per_gross_ha_life",
	"custom_sf_stems_per_gross_ha_year",
	"custom_sf_best_year_stems_per_gross_ha",
]


def _drop_field(doctype, fieldname):
	name = "%s-%s" % (doctype, fieldname)
	if not frappe.db.exists("Custom Field", name):
		return False
	# force so a field still sitting in a stale field_order does not block it.
	frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)
	return True


def _drop_setter(doctype, fieldname, prop):
	filters = {"doc_type": doctype, "property": prop}
	filters["field_name"] = fieldname if fieldname else ["in", ["", None]]
	gone = 0
	for name in frappe.get_all("Property Setter", filters=filters, pluck="name"):
		frappe.delete_doc("Property Setter", name, force=True, ignore_permissions=True)
		gone += 1
	return gone


def execute():
	dropped, cleared, setters = [], [], 0

	for fieldname in DEMAND_FIELDS:
		if _drop_field("Summer Flower Market Demand", fieldname):
			dropped.append("Summer Flower Market Demand.%s" % fieldname)

	for doctype, fieldname, prop in DEMAND_SETTERS:
		setters += _drop_setter(doctype, fieldname, prop)

	for fieldname in DEMAND_DESCRIPTIONS:
		name = "Summer Flower Market Demand-%s" % fieldname
		if frappe.db.exists("Custom Field", name) and frappe.db.get_value(
			"Custom Field", name, "description"
		):
			frappe.db.set_value("Custom Field", name, "description", "")
			cleared.append(fieldname)

	for doctype, fieldname, prop in PROTOCOL_SETTERS:
		setters += _drop_setter(doctype, fieldname, prop)

	for fieldname in PROTOCOL_FIELDS:
		if _drop_field("Crop Protocol", fieldname):
			dropped.append("Crop Protocol.%s" % fieldname)

	if dropped or cleared or setters:
		frappe.clear_cache(doctype="Summer Flower Market Demand")
		frappe.clear_cache(doctype="Crop Protocol")
		print("retired %d custom fields, %d property setters, %d descriptions"
		      % (len(dropped), setters, len(cleared)))
		for d in dropped:
			print("   dropped %s" % d)
