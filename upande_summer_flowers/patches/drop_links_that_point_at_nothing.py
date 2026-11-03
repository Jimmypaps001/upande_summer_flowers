"""Remove Connections links that point at a doctype or field the site lacks.

A customisation file ADDS and UPDATES DocType Link rows; it never deletes them.
So taking a link out of the shipped JSON leaves it in place on every site that
already had it, and a link whose field does not exist on this site is not
inert -- get_open_count builds a WHERE on that column and the whole Connections
tab dies with it:

    Unknown column 'custom_crop_cycle' in 'WHERE'

That is exactly what Marginpar hit. The link came from this bench's Stock
Entry, which carries a "Trial Batch (Crop Cycle)" field from another app
entirely; the shipped file stopped listing it, and the row stayed behind.

Only the doctypes we ship links for are touched, and only rows that are
structurally dead: no such doctype, or no such field on it. A row that is
merely unused is left alone -- it still works.
"""

import frappe

# The doctypes whose Connections we ship. Everything else on the site is
# somebody else's to clean.
OURS = [
	"Summer Flower Market Demand", "Summer Flower Production Plan",
	"Summer Flower Procurement Plan", "Summer Flower Propagation Plan",
	"Summer Flower Motherstock Plan", "Summer Flower Motherstock Batch",
	"Summer Flower Budget", "Crop Protocol Version", "Planting Calendar",
	"Crop Protocol", "Crop Cycle", "Block",
]


def _has_field(doctype, fieldname):
	if not fieldname:
		return True          # a link with no field is not this patch's business
	try:
		return bool(frappe.get_meta(doctype).has_field(fieldname))
	except Exception:
		return False


def execute():
	rows = frappe.get_all(
		"DocType Link",
		filters={"parent": ["in", OURS], "parenttype": "DocType"},
		fields=["name", "parent", "link_doctype", "parent_doctype",
		        "link_fieldname", "table_fieldname", "is_child_table"],
	)
	dropped = []
	for r in rows:
		why = None
		if not frappe.db.exists("DocType", r.link_doctype):
			why = "no such doctype"
		elif r.parent_doctype and not frappe.db.exists("DocType", r.parent_doctype):
			why = "no such parent doctype"
		elif not _has_field(r.link_doctype, r.link_fieldname):
			why = "no field %s on %s" % (r.link_fieldname, r.link_doctype)
		elif r.parent_doctype and not _has_field(r.parent_doctype, r.table_fieldname):
			why = "no table %s on %s" % (r.table_fieldname, r.parent_doctype)
		if why:
			frappe.db.delete("DocType Link", {"name": r.name})
			dropped.append((r.parent, r.link_doctype, why))

	if dropped:
		for parent, link, why in dropped:
			print("  dropped %s -> %s (%s)" % (parent, link, why))
		frappe.clear_cache()
	print("Connections links checked: %d, dropped: %d" % (len(rows), len(dropped)))
