"""Every loss allowance on a summer flower protocol goes to zero."""

import frappe
from frappe.utils import cint

# field on Crop Protocol -> field on Crop Protocol Version -> value meaning "none"
ZERO = [
	("custom_sf_tc_order_loss_pct", "tc_order_loss_pct", 0),
	("custom_sf_cutting_reject_pct", "cutting_reject_pct", 0),
	("custom_sf_rooting_success_pct", "rooting_success_pct", 100),
	("custom_sf_field_establishment_pct", "field_establishment_pct", 100),
]


def execute():
	# A patch that raises rolls back every doctype change in the migrate with
	if not frappe.db.has_column("Crop Protocol", "custom_is_summer_flower"):
		return

	protocols = frappe.get_all("Crop Protocol",
	                           filters={"custom_is_summer_flower": 1}, pluck="name")
	if not protocols:
		return

	changed_p = 0
	for name in protocols:
		hit = False
		for pf, _vf, val in ZERO:
			if frappe.db.get_value("Crop Protocol", name, pf) != val:
				frappe.db.set_value("Crop Protocol", name, pf, val,
				                    update_modified=False)
				hit = True
		changed_p += 1 if hit else 0

	versions = frappe.get_all("Crop Protocol Version",
	                          filters={"crop_protocol": ["in", protocols]},
	                          pluck="name")
	changed_v = 0
	for name in versions:
		hit = False
		for _pf, vf, val in ZERO:
			if frappe.db.get_value("Crop Protocol Version", name, vf) != val:
				frappe.db.set_value("Crop Protocol Version", name, vf, val,
				                    update_modified=False)
				hit = True
		# cuttings_per_plant_required is derived from rooting x field establishment,
		if frappe.db.get_value("Crop Protocol Version", name,
		                       "cuttings_per_plant_required") != 1:
			frappe.db.set_value("Crop Protocol Version", name,
			                    "cuttings_per_plant_required", 1,
			                    update_modified=False)
			hit = True
		changed_v += 1 if hit else 0

	frappe.db.commit()
	print("zeroed the losses on %d of %d protocols and %d of %d versions"
	      % (changed_p, len(protocols), changed_v, len(versions)))
