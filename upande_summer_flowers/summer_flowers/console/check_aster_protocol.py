"""Everything the Aster Pink Flash protocol says, and what it leaves blank."""

import frappe
from frappe.utils import cint, flt

NAME = "Aster Pink Flash-Karen"

GROUPS = {
	"identity": ["variety", "farm", "company", "custom_is_summer_flower",
	             "custom_sf_growing_cycle", "custom_sf_crop_category"],
	"density": ["plants_per_sqm", "custom_sf_min_planting_area_sqm",
	            "custom_sf_sqm_net_per_bed", "custom_sf_plants_per_bed",
	            "custom_sf_min_planting_beds_derived"],
	"timeline": ["weeks_to_pinch", "custom_sf_first_harvest_offset_weeks",
	             "weeks_between_flushes", "total_weeks_in_ground",
	             "custom_sf_turnaround_weeks", "total_flushes",
	             "total_stems_per_plant_life", "life_expectancy_years",
	             "yield_stems_per_ha", "custom_sf_harvest_weeks_per_year"],
	"propagation": ["custom_sf_weeks_on_tray", "custom_sf_weeks_on_pot",
	                "custom_sf_hardening_weeks", "custom_sf_ramp_weeks",
	                "custom_sf_ramp_profile", "custom_sf_establishment_includes_ramp",
	                "custom_sf_establishment_includes_hardening",
	                "custom_sf_sticking_to_planting_weeks",
	                "custom_sf_cutting_to_harvest_weeks"],
	"multiplication": ["custom_sf_max_multiplication_cycles",
	                   "custom_sf_multiplication_factor_per_cycle",
	                   "custom_sf_cycle_time_weeks", "custom_sf_tc_order_loss_pct",
	                   "custom_sf_motherstock_life_weeks",
	                   "custom_sf_cuttings_per_plant_per_week",
	                   "custom_sf_plants_per_sqm_bench", "custom_sf_plants_per_pot",
	                   "custom_sf_pots_per_sqm"],
	"losses": ["custom_sf_rooting_success_pct", "custom_sf_field_establishment_pct",
	           "custom_sf_cutting_reject_pct", "custom_sf_max_stems_per_plant_per_cut"],
}


def run():
	d = frappe.get_doc("Crop Protocol", NAME)
	print("%s  (status %s)" % (d.name, d.get("custom_sf_protocol_status")))
	blank = []
	for group, fields in GROUPS.items():
		print("\n[%s]" % group)
		for f in fields:
			if not d.meta.has_field(f):
				print("   %-44s -- NO SUCH FIELD" % f)
				continue
			v = d.get(f)
			flag = ""
			if v in (None, "", 0, 0.0):
				flag = "   <-- blank"
				blank.append(f)
			print("   %-44s %s%s" % (f, v, flag))
	print("\n[material route]")
	for r in (d.get("custom_sf_material_route") or []):
		print("   %-6s %-16s weeks %-3s loss %-6s buy %-2s yields %-5s standing %s"
		      % (r.row_type, r.stage or r.step, r.weeks, r.loss_pct,
		         r.is_purchase, r.yields_per_unit, r.get("is_standing")))
	print("\n[flush schedule] %d rows" % len(d.get("custom_sf_flush_schedule") or []))
	tot = sum(flt(r.stems_per_plant) for r in (d.get("custom_sf_flush_schedule") or []))
	print("   stems per plant over life: %.2f" % tot)
	print("\n[grades] %d rows, total %s%%"
	      % (len(d.get("custom_sf_grade_allocation") or []),
	         sum(flt(r.allocation_pct) for r in (d.get("custom_sf_grade_allocation") or []))))
	print("\nBLANK: %s" % ", ".join(blank))
