"""What an approved procurement plan's dashboard says."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())


def _run():
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import overview

	name = frappe.db.get_value("Summer Flower Procurement Plan", {"docstatus": 1},
	                           "name", order_by="creation desc")
	if not name:
		print("no approved procurement plan")
		return
	o = overview(name)
	b, dm, g, w = o["buying"], o["demand"], o["ground"], o["when"]
	print("%s — %s at %s (%s)" % (o["plan"], o["variety"], o["farm"], o["status"]))
	print("\nbuying")
	print("   %s %s%s" % (f"{b['units']:,}", b["entry_stage"] or b["method"],
	                      "  (set by hand, sum said %s)" % f"{b['calculated']:,}"
	                      if b["overridden"] else ""))
	print("   order by %s%s" % (b["order_by"], "  ALREADY PASSED" if b["order_late"] else ""))
	if b["propagates_here"]:
		print("   pool %s mothers · %s cuttings/wk · %s cycles · %s cutting weeks left"
		      % (f"{b['pool']:,}", f"{b['weekly_draw']:,}", b["cycles"],
		         b["cutting_weeks"]))
	print("\ndemand")
	print("   %s of %s stems = %s%%"
	      % (f"{dm['planned']:,}", f"{dm['asked']:,}", round(dm["coverage_pct"], 1)))
	if dm["at_risk"]:
		print("   %s stems have no cuttings -> %s%% after that"
		      % (f"{dm['at_risk']:,}", dm["after_risk_pct"]))
	print("\nground")
	print("   %s plants · %s plantings · %s beds of %s"
	      % (f"{g['plants']:,}", g["plantings"], g["beds"], f"{g['beds_available']:,}"))
	print("\nwhen")
	for k in ("first_sticking", "first_planting", "last_planting",
	          "first_harvest_week", "last_harvest_week"):
		print("   %-20s %s" % (k, w[k] or "—"))
	print("\nfirst 6 cohorts:")
	print("   %-10s %-12s %-12s %-12s %-10s %-8s %s"
	      % ("plant wk", "order by", "arrives", "planted", "harvest", "plants", "to buy"))
	for a in o["arrivals"][:6]:
		print("   %-10s %-12s %-12s %-12s %-10s %-8s %s"
		      % (a["planting_week"], a["order_by"] or "—", a["arrives"] or "—",
		         a["planting_date"] or "—", a["first_harvest_week"] or "—",
		         f"{a['plants']:,}",
		         "off the pool" if a["from_pool"] else f"{a['units']:,}"))
