"""Put the Aster Pink Flash protocol into a state worth demonstrating.

Structural first, because those are plainly wrong rather than a matter of
opinion: the route reads TC -> a stray Step called "Plants" -> Plants, which
says the crop is bought as tissue culture and planted straight out. It is not.
The farm's own Crop Categories document has every Aster of this group under
"TC to motherstock", and the flush schedule on this very protocol -- eight
flushes over 111 weeks off cuttings -- only makes sense with a pool behind it.

The loss figures are the farm's to confirm. They are set to the ones this work
has been using so the demo shows the arithmetic doing something, and printed
at the end as assumptions, not facts.
"""

import frappe
from frappe.utils import cint, flt

NAME = "Aster Pink Flash-Karen"

# Route for every Aster of this group in the categories document of 14 August.
ROUTE = ("TC", "Motherstock", "Plants")

# To confirm with an agronomist. Said out loud at the end of the run.
ASSUMED = {
	"custom_sf_rooting_success_pct": 90.0,
	"custom_sf_field_establishment_pct": 95.0,
	"custom_sf_cutting_reject_pct": 14.0,
	"custom_sf_cuttings_per_plant_per_week": 1.0,
	"custom_sf_cycle_time_weeks": 11,
}


def run(dry_run=1):
	dry_run = cint(dry_run)
	d = frappe.get_doc("Crop Protocol", NAME)
	changes = []

	# ---- the route
	have = [(r.row_type or "Stage", r.stage or r.step)
	        for r in (d.get("custom_sf_material_route") or [])]
	want = [("Stage", s) for s in ROUTE]
	if have != want:
		changes.append("route %s -> %s"
		               % (" / ".join(x[1] or "?" for x in have),
		                  " -> ".join(ROUTE)))
		d.set("custom_sf_material_route", [])
		for stage in ROUTE:
			# weeks, loss, yields, is_purchase and is_standing are all derived on
			# save. Only the stages and their order are typed.
			d.append("custom_sf_material_route", {"row_type": "Stage", "stage": stage})

	# ---- the growing cycle
	if not d.get("custom_sf_growing_cycle"):
		changes.append("growing cycle -> Perennial - distinct flushes")
		d.custom_sf_growing_cycle = "Perennial - distinct flushes"

	# ---- the figures the arithmetic needs
	for field, value in ASSUMED.items():
		if not d.meta.has_field(field):
			changes.append("!! no field %s" % field)
			continue
		if flt(d.get(field)) != flt(value):
			changes.append("%s %s -> %s" % (field, d.get(field), value))
			d.set(field, value)

	print("%s" % NAME)
	for c in changes:
		print("   %s" % c)
	if not changes:
		print("   nothing to change")
		return

	if dry_run:
		print("\n   (dry run -- nothing saved; pass dry_run=0 to apply)")
		frappe.db.rollback()
		return

	d.flags.ignore_permissions = True
	d.save()
	frappe.db.commit()
	print("\n   saved. route now:")
	d.reload()
	for r in d.custom_sf_material_route:
		print("      %-6s %-14s weeks %-3s loss %-6s buy %-2s yields %-5s standing %s"
		      % (r.row_type, r.stage, r.weeks, r.loss_pct, r.is_purchase,
		         r.yields_per_unit, r.get("is_standing")))
	print("\n   ASSUMED, to confirm with an agronomist:")
	for field, value in ASSUMED.items():
		print("      %-44s %s" % (field, value))
