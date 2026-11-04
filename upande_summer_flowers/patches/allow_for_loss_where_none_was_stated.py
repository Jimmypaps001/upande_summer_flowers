"""Fill in a 5% allowance on stages that state no loss at all.

Thirty-three of the Active protocol versions here carry 100% rooting and 100%
field establishment with 0% grading rejection. That is not a measurement. It
is the field never having been filled in, and every order sized from it is
short by whatever really dies -- which is why seed crops looked carefully
costed and everything else looked free.

So a house allowance stands in: 5% at rooting, 5% at establishment, 5% at
grading, until somebody measures the variety. It is written onto the protocol
AND onto the version in force, because a figure that shows one number on the
form and another on the card is the fault this app keeps having to fix.

Only where nothing was said. A protocol stating 95%, or 80%, or any number
other than a bare 100 keeps it: this never argues with a measurement.

Nothing is invented beyond the allowance itself, and it is reversible -- set
the real figure on the protocol and approve it, and this never touches it
again.
"""

import frappe
from frappe.utils import flt

ALLOWANCE = 5.0


def _needs(value):
	"""True when the field says nothing: blank, zero, or a bare 100%."""
	v = flt(value)
	return (not v) or v == 100.0


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
	"""The version in force, so the allowance reaches the plans built on it.

	Superseded versions are left exactly as they are. They are the record of
	what a plan was approved against, and rewriting one would change what an
	approved plan says it was built on -- the single thing the snapshot exists
	to guarantee.
	"""
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
		# gross an order up by a survival rate nothing else agrees with.
		survival = (root / 100.0) * (field / 100.0)
		vals["cuttings_per_plant_required"] = round(1.0 / survival, 4) if survival else 1
		frappe.db.set_value("Crop Protocol Version", r.name, vals,
		                    update_modified=False)
		n += 1
	return n
