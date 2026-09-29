"""The 52-week schedule and its generations, as the dialog will draw it."""

import frappe
from frappe.utils import cint


def main(plan=None, divert=None, weeks=14):
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	if not plan:
		for row in frappe.get_all("Summer Flower Production Plan",
		                          filters={"docstatus": ["<", 2]}, fields=["name"],
		                          order_by="modified desc", limit_page_length=40):
			d = frappe.get_doc("Summer Flower Production Plan", row.name)
			if d.get("protocol") and ps.recommend(d).get("solvable"):
				plan = row.name
				break
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	r = ps.recommend(p, divert_weeks=divert)
	c = r.get("chosen")
	if not c:
		print("nothing to show: %s" % r.get("reason"))
		return
	print("%s -- %s at %s" % (plan, r["variety"], r["farm"]))
	print("  %s plantlets, cut diverted for %s weeks, order by %s"
	      % (f"{c['tc']:,}", c["divert_weeks"], c["order_by"]))
	print("  need %s cuttings over %s weeks"
	      % (f"{r['need']['cuttings']:,}", r["need"]["weeks"]))

	print("\n  GENERATIONS")
	print("    gen  mothers    arrivals  starts cutting  cutting wks  cleared")
	for g in c.get("generations") or []:
		print("     %-3s %9s %9s  %-15s %11s  %s"
		      % (g["generation"], f"{g['mothers']:,}", g["arrivals"],
		         g["first_cut_date"], g["cutting_weeks"], g["expiry_date"]))

	t = c.get("weeks_table") or []
	print("\n  WEEKS (%d rows; first %d and any with an event)" % (len(t), cint(weeks)))
	print("    wk  date        live   standing   cut    ->mult  ->field  cum     need  short  event")
	for w in t:
		if w["week_no"] > cint(weeks) and not w["event"] and not w["demand"]:
			continue
		print("    %3s  %-11s %-6s %8s %7s %7s %7s %8s %6s %6s  %s"
		      % (w["week_no"], w["week_start"], w["generations_live"],
		         f"{w['mothers_standing']:,}", f"{w['cuttings_cut']:,}",
		         f"{w['to_multiplication']:,}", f"{w['to_field']:,}",
		         f"{w['cumulative_to_field']:,}", f"{w['demand']:,}" if w["demand"] else "",
		         f"{w['shortfall']:,}" if w["shortfall"] else "", w["event"]))
