"""What Aster Pink Flash's demand actually peaks at. Read-only."""

import frappe
from frappe.utils import cint, flt


def main():
	md = frappe.get_all("Summer Flower Market Demand",
	                    filters={"variety": ["like", "%Pink Flash%"]},
	                    fields=["name", "variety", "farm", "total_demand_stems",
	                            "peak_weekly_demand", "weeks_covered"],
	                    limit_page_length=0)
	for r in md:
		print("%-40s %-10s total %12s peak/wk %10s over %s weeks"
		      % (r.name[:40], r.farm, f"{r.total_demand_stems:,}",
		         f"{r.peak_weekly_demand:,}", r.weeks_covered))

	plans = frappe.get_all("Summer Flower Production Plan",
	                       filters={"variety": ["like", "%Pink Flash%"],
	                                "docstatus": ["<", 2]},
	                       fields=["name", "farm", "season", "protocol",
	                               "new_plants_required",
	                               "peak_weekly_sticking_planned",
	                               "peak_sticking_week_planned",
	                               "total_demand_stems"],
	                       order_by="modified desc", limit_page_length=4)
	print("\nproduction plans:")
	for p in plans:
		print("   %-20s %-9s plants %9s  peak sticking %8s in %s"
		      % (p.name, p.season, f"{cint(p.new_plants_required):,}",
		         f"{cint(p.peak_weekly_sticking_planned):,}",
		         p.peak_sticking_week_planned))

	if not plans:
		return
	v = frappe.get_cached_doc("Crop Protocol Version", plans[0].protocol)
	print("\nprotocol %s:" % v.name)
	for f in ("cuttings_per_plant_per_week", "cuttings_per_plant_required",
	          "min_planting_area_sqm", "plants_per_sqm", "plants_per_bed",
	          "sqm_net_per_bed", "ms_establishment_weeks", "motherstock_life_weeks",
	          "max_multiplication_cycles", "multiplication_factor_per_cycle",
	          "weeks_on_tray", "weeks_on_pot", "supplier_lead_weeks"):
		print("   %-34s %s" % (f, v.get(f)))
	print("   weeks_tc_to_first_cut()           %s" % v.weeks_tc_to_first_cut())
	print("   multiplication_factor(1/2/3)      %s / %s / %s"
	      % (v.multiplication_factor(1), v.multiplication_factor(2),
	         v.multiplication_factor(3)))
