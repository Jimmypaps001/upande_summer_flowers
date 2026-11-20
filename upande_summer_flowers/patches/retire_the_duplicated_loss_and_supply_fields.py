"""Drop the protocol-wide loss and supply fields. The road and the table hold them now.

A figure typed in two places is a question about which one is true, and the
answer was never on screen. Losses belong to the road -- tissue culture and
bare roots do not lose the same things -- and how often a house comes belongs
to the row for that house. Both now have exactly one home.

one_place_for_losses_and_houses ran first and carried every figure onto the
road and into the table, so nothing is lost here; this removes the columns that
were left behind. The Version keeps the three loss figures, but READ-ONLY and
worked out from the roads, because a great deal still needs one answer for the
crop and it should be the roads' answer rather than the first road's.
"""

import frappe

GONE = (
	"custom_sf_rooting_success_pct",
	"custom_sf_field_establishment_pct",
	"custom_sf_tc_order_loss_pct",
	"custom_sf_loss_section",
	"custom_sf_cb_loss_a",
	"custom_sf_supplier_cycles_per_year",
	"custom_sf_suppliers_available",
)


def execute():
	dropped = 0
	for fieldname in GONE:
		name = frappe.db.get_value("Custom Field",
		                           {"dt": "Crop Protocol", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, force=True,
			                  ignore_permissions=True, delete_permanently=True)
			dropped += 1
	# Property setters that pointed at them would otherwise sit there naming a
	# field nobody can see, and migrate re-applies them every time.
	stale = frappe.get_all("Property Setter",
	                       filters={"doc_type": "Crop Protocol",
	                                "field_name": ["in", GONE]}, pluck="name")
	for name in stale:
		frappe.delete_doc("Property Setter", name, force=True,
		                  ignore_permissions=True, delete_permanently=True)
	frappe.clear_cache(doctype="Crop Protocol")
	frappe.db.commit()
	print("retired %d field(s) and %d property setter(s) from Crop Protocol"
	      % (dropped, len(stale)))
