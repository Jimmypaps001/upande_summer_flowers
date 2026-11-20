"""Drop the columns the retired fields left behind.

Frappe removes a Custom Field from the form and the meta, but never drops the
column underneath it: the data sits there, invisible, readable by anything
that still names it. For a doctype that is meant to be clean that is exactly
the half-measure we set out to avoid, so the columns go too.

This is not reversible, so it refuses unless the figures it is about to throw
away have demonstrably been carried somewhere else: every route must state its
own losses, and every protocol that named a number of houses must have the rows
to match. one_place_for_losses_and_houses does both; this only confirms it.
"""

import frappe

COLUMNS = {
	"Crop Protocol": (
		"custom_sf_rooting_success_pct",
		"custom_sf_field_establishment_pct",
		"custom_sf_tc_order_loss_pct",
		"custom_sf_supplier_cycles_per_year",
		"custom_sf_suppliers_available",
	),
	"Crop Protocol Version": (
		"suppliers_available",
		"supplier_cycles_per_year",
	),
}


def execute():
	if not _carried():
		return
	dropped = []
	for doctype, columns in COLUMNS.items():
		table = "tab%s" % doctype
		have = {c.Field for c in frappe.db.sql("desc `%s`" % table, as_dict=True)}
		for column in columns:
			if column not in have:
				continue
			frappe.db.sql_ddl("alter table `%s` drop column `%s`" % (table, column))
			dropped.append("%s.%s" % (doctype, column))
	frappe.db.commit()
	frappe.clear_cache()
	print("dropped %d retired column(s): %s"
	      % (len(dropped), ", ".join(dropped) or "none"))


def _carried():
	"""Refuse while anything still depends on what is about to be dropped."""
	bare_ways = frappe.db.sql("""
		select count(*) from `tabCrop Protocol Way`
		where parenttype = 'Crop Protocol'
		  and not (ifnull(order_loss_pct, 0) or ifnull(rooting_success_pct, 0)
		           or ifnull(field_establishment_pct, 0))""")[0][0]
	if bare_ways:
		print("NOT dropping: %d route(s) still state no losses of their own. "
		      "Run one_place_for_losses_and_houses first." % bare_ways)
		return False
	# A protocol that said how many houses it had must now have the rows.
	short = frappe.db.sql("""
		select count(*) from `tabCrop Protocol` cp
		where cp.custom_is_summer_flower = 1
		  and ifnull(cp.custom_sf_suppliers_available, 0) > 0
		  and not exists (select 1 from `tabSummer Flower Protocol Supplier` s
		                  where s.parent = cp.name)""")[0][0]
	if short:
		print("NOT dropping: %d protocol(s) named a house count with no rows "
		      "to match it." % short)
		return False
	return True
