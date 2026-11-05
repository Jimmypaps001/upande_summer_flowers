"""Fill in a 5% allowance on stages that state no loss at all."""

import frappe
from frappe.utils import flt

ALLOWANCE = 5.0


def _needs(value):
	"""True when the field says nothing at all: blank or zero.

	Not 100. The field has no default, so an untouched protocol is blank;
	a 100 is somebody saying this stage loses nothing, and the allowance
	does not argue with an answer."""
	return not flt(value)


def execute():
	protocols = _fill_protocols()
	versions = _fill_versions()
	print("5%% allowance written: %d protocols, %d versions in force"
	      % (protocols, versions))


def _fill_protocols():
	rows = frappe.get_all(
		"Crop Protocol", filters={"custom_is_summer_flower": 1},
		fields=["name", "custom_sf_rooting_success_pct",
		        "custom_sf_field_establishment_pct", "custom_sf_cutting_reject_pct"])
	n = 0
	for r in rows:
		vals = {}
		if _needs(r.custom_sf_rooting_success_pct):
			vals["custom_sf_rooting_success_pct"] = 100.0 - ALLOWANCE
		if _needs(r.custom_sf_field_establishment_pct):
			vals["custom_sf_field_establishment_pct"] = 100.0 - ALLOWANCE
		if not flt(r.custom_sf_cutting_reject_pct):
			vals["custom_sf_cutting_reject_pct"] = ALLOWANCE
		if vals:
			frappe.db.set_value("Crop Protocol", r.name, vals, update_modified=False)
			n += 1
	return n


def _fill_versions():
	"""The version in force, so the allowance reaches the plans built on it."""
	rows = frappe.get_all(
		"Crop Protocol Version", filters={"version_status": "Active"},
		fields=["name", "rooting_success_pct", "field_establishment_pct",
		        "cutting_reject_pct"])
	n = 0
	for r in rows:
		vals = {}
		root = flt(r.rooting_success_pct)
		field = flt(r.field_establishment_pct)
		if _needs(root):
			root = 100.0 - ALLOWANCE
			vals["rooting_success_pct"] = root
		if _needs(field):
			field = 100.0 - ALLOWANCE
			vals["field_establishment_pct"] = field
		if not flt(r.cutting_reject_pct):
			vals["cutting_reject_pct"] = ALLOWANCE
		if not vals:
			continue
		# Derived from the two above, so it moves with them or the card would
		survival = (root / 100.0) * (field / 100.0)
		vals["cuttings_per_plant_required"] = round(1.0 / survival, 4) if survival else 1
		frappe.db.set_value("Crop Protocol Version", r.name, vals,
		                    update_modified=False)
		n += 1
	return n
