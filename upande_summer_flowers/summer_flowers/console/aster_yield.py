"""What one Aster Pink Flash plant yields, and when. Read-only."""

import frappe
from frappe.utils import cint, flt


def main(version="Aster Pink Flash-Karen-v17"):
	v = frappe.get_cached_doc("Crop Protocol Version", version)
	print("%s  (%s)" % (version, v.growing_cycle))
	for f in ("weeks_planting_to_first_cut", "productive_weeks",
	          "stems_per_plant_per_week", "total_stems_per_plant_life",
	          "stated_stems_per_plant_life", "harvest_weeks_per_year",
	          "sticking_to_planting_weeks", "cuttings_per_plant_required",
	          "first_harvest_offset_weeks", "flush_interval_weeks",
	          "total_weeks_in_ground"):
		print("   %-32s %s" % (f, v.get(f)))
	off = v.flush_offsets()
	print("\n   flush_offsets: %d entries" % len(off))
	for w, s in off[:8]:
		print("      week %-3s after planting  %.3f stems/plant" % (w, s))
	if len(off) > 8:
		print("      ... and %d more" % (len(off) - 8))
	print("   total stems per plant over its life: %.2f" % sum(s for _w, s in off))
