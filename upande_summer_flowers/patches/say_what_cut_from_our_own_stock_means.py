"""Rename "Raised From A Standing Pool" to "Cut from our own stock".

The flag answers a question the farm asks in plain words -- do we keep a
mother block for this crop, or is it planted from what arrives -- and it was
named after the mechanism rather than the question. It was also hidden, so
the answer was in the database and nowhere a person could read it. It is now
on the form, still read-only, because it is worked out from the standing
stage on the material route and typing it would only disagree with the route.

Both copies move: the protocol's own field and the snapshot the Version
keeps. Migrate has already made the new column from the JSON by the time this
runs, so this carries the answers across and takes the old column away --
Frappe drops neither on its own, and a column nobody reads that still holds
an answer is the half-measure we keep setting out to avoid.

On a site built after the rename there is nothing to carry and nothing to
drop, and it says so.
"""

import frappe

MOVES = (
	("Crop Protocol", "custom_sf_raised_from_pool", "custom_sf_cut_from_own_stock"),
	("Crop Protocol Version", "raised_from_pool", "cut_from_own_stock"),
)


def _columns(doctype):
	return {c.Field for c in frappe.db.sql("desc `tab%s`" % doctype, as_dict=True)}


def execute():
	carried = dropped = 0
	for doctype, old, new in MOVES:
		have = _columns(doctype)
		if old not in have:
			continue
		if new not in have:
			# The JSON should have made it; if it did not, renaming the column
			# is the move -- never drop the only copy of the answer.
			frappe.db.sql_ddl("alter table `tab%s` change `%s` `%s` int(1) "
			                  "not null default 0" % (doctype, old, new))
			carried += 1
			continue
		frappe.db.sql("update `tab%s` set `%s` = `%s`" % (doctype, new, old))
		frappe.db.sql_ddl("alter table `tab%s` drop column `%s`" % (doctype, old))
		carried += 1
		dropped += 1

	# The Custom Field and anything pointing at it by its old name.
	name = frappe.db.get_value("Custom Field", {"dt": "Crop Protocol",
	                                            "fieldname": "custom_sf_raised_from_pool"})
	if name:
		frappe.delete_doc("Custom Field", name, force=True,
		                  ignore_permissions=True, delete_permanently=True)
	stale = frappe.get_all("Property Setter",
	                       filters={"doc_type": "Crop Protocol",
	                                "field_name": "custom_sf_raised_from_pool"},
	                       pluck="name")
	for n in stale:
		frappe.delete_doc("Property Setter", n, force=True,
		                  ignore_permissions=True, delete_permanently=True)
	frappe.clear_cache(doctype="Crop Protocol")
	frappe.clear_cache(doctype="Crop Protocol Version")
	frappe.db.commit()
	print("cut from our own stock: %d column(s) carried, %d old column(s) "
	      "dropped, %d custom field(s) and %d property setter(s) retired"
	      % (carried, dropped, 1 if name else 0, len(stale)))
