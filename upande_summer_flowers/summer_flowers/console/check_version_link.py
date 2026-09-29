"""Does editing a Crop Protocol reach the plans that already exist? Read-only."""

import frappe
from frappe.utils import cint


def main(protocol="Aster Pink Flash-Karen"):
	vs = frappe.get_all("Crop Protocol Version",
	                    filters={"crop_protocol": protocol},
	                    fields=["name", "creation", "tc_order_loss_pct",
	                            "supplier_lead_weeks", "cuttings_per_plant_per_week"],
	                    order_by="creation desc", limit_page_length=0)
	print("%d versions of %s" % (len(vs), protocol))
	for v in vs[:6]:
		print("   %-34s %s  loss=%s lead=%s cut/wk=%s"
		      % (v.name, str(v.creation)[:19], v.tc_order_loss_pct,
		         v.supplier_lead_weeks, v.cuttings_per_plant_per_week))

	print("\n  Which version each production plan is pinned to:")
	plans = frappe.get_all("Summer Flower Production Plan",
	                       filters={"docstatus": ["<", 2]},
	                       fields=["name", "protocol", "variety",
	                               "custom_sf_protocol_status"]
	                       if frappe.get_meta("Summer Flower Production Plan")
	                       .get_field("custom_sf_protocol_status")
	                       else ["name", "protocol", "variety"],
	                       limit_page_length=0)
	mine = [p for p in plans if p.protocol in {v.name for v in vs}]
	latest = vs[0].name if vs else None
	for p in mine[:10]:
		print("   %-20s -> %-34s %s"
		      % (p.name, p.protocol,
		         "current" if p.protocol == latest else "OLDER VERSION"))
	stale = [p for p in mine if p.protocol != latest]
	print("\n  %d of %d plans on this protocol are pinned to an older version."
	      % (len(stale), len(mine)))
	print("  Editing the Crop Protocol and approving it mints a NEW version; a plan")
	print("  already made keeps the one it was built from until it is regenerated.")
