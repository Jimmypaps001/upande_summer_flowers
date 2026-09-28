"""The season on one page. Rolls back."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback()[-800:])
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_season_plan.summer_flower_season_plan import for_season

	p = frappe.db.get_value("Summer Flower Production Plan",
	                        {"variety": "Aster Pink Flash", "farm": "Karen",
	                         "docstatus": 1},
	                        ["name", "variety", "farm", "season_start_year"],
	                        as_dict=True, order_by="creation desc")
	name = for_season(p.variety, p.farm, p.season_start_year, p.name)
	d = frappe.get_doc("Summer Flower Season Plan", name)
	print("%s" % d.name)
	print("   %s" % d.summary)
	print("\n   first plants arrive %s | planting %s to %s | first cut %s to %s"
	      % (d.first_arrival, d.first_planting, d.last_planting,
	         d.first_cut, d.last_cut))
	print("   %s plantings · %s beds · %s plants · %s blocks · %s planted so far"
	      % (d.plantings, d.beds, f"{cint(d.plants):,}", d.blocks_used,
	         d.planted_so_far))
	print("\n   %-26s %-5s %-8s %-13s %-13s %-9s %s"
	      % ("block", "beds", "plants", "plants arrive", "plant on", "first cut",
	         "which beds"))
	for r in d.rows[:8]:
		print("   %-26s %-5s %-8s %-13s %-13s %-9s %s"
		      % ((r.block or "—").split(" - ")[-1][:26], r.beds,
		         f"{cint(r.plants):,}", r.plants_arrive or "—",
		         r.plant_on or "—", r.first_cut or "—",
		         (r.bed_numbers or "—")[:22]))
	print("\n   ... %s rows in total" % len(d.rows))
