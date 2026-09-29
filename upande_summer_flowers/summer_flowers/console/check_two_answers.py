"""The same question, answered twice: what does each document say to buy?"""

import frappe
from frappe.utils import cint


def main(plan="SFPP-2026-00061"):
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	print("%s -- %s at %s, protocol %s" % (plan, p.variety, p.farm, p.protocol))
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	print("\nPROCUREMENT PLAN(S)")
	for r in frappe.get_all("Summer Flower Procurement Plan",
	                        filters={"production_plan": plan},
	                        fields=["name", "docstatus", "pool_plants",
	                                "divert_weeks", "multiplication_cycles",
	                                "calculated_units", "motherstock_plan",
	                                "modified"], limit_page_length=0):
		est = frappe.get_all("Planting Material Requirement",
		                     filters={"parent": r.name, "line_type": "Establishment"},
		                     fields=["qty_to_order", "order_by_date"])
		print("  %s  docstatus=%s  units=%s  pool=%s  divert=%s  cycles=%s  modified %s"
		      % (r.name, r.docstatus,
		         f"{cint(est[0].qty_to_order):,}" if est else "-",
		         f"{cint(r.pool_plants):,}", r.divert_weeks, r.multiplication_cycles,
		         str(r.modified)[:16]))

	print("\nPROPAGATION PLAN(S)")
	for r in frappe.get_all("Summer Flower Propagation Plan",
	                        filters={"production_plan": plan},
	                        fields=["name", "tc_plants_required",
	                                "mother_plants_required", "peak_weekly_cuttings",
	                                "cuttings_uncovered", "new_pool_cutting_weeks",
	                                "new_pool_expiry", "modified"],
	                        limit_page_length=0):
		print("  %s  tc=%s  pool=%s  peak_weekly=%s  uncovered=%s  cutting_weeks=%s  expiry=%s"
		      % (r.name, f"{cint(r.tc_plants_required):,}",
		         f"{cint(r.mother_plants_required):,}",
		         f"{cint(r.peak_weekly_cuttings):,}",
		         f"{cint(r.cuttings_uncovered):,}", r.new_pool_cutting_weeks,
		         r.new_pool_expiry))

	print("\nHOW EACH GOT THERE")
	peak = cint(p.peak_weekly_sticking_planned)
	factor = v.multiplication_factor()
	print("  propagation plan: pool = the busiest week's cuttings = %s" % f"{peak:,}")
	print("                    tc   = pool / multiplication_factor(%s) = %s"
	      % (factor, f"{int(peak / factor) if factor else peak:,}"))
	print("                    cutting weeks = life %s - est %s x (cycles %s - 1) = %s"
	      % (cint(v.motherstock_life_weeks), cint(v.weeks_tc_to_first_cut()),
	         cint(v.max_multiplication_cycles),
	         cint(v.motherstock_life_weeks)
	         - cint(v.weeks_tc_to_first_cut())
	         * max(0, cint(v.max_multiplication_cycles) - 1)))
	r = ps.recommend(p)
	c = r.get("chosen") or {}
	print("  solver:           tc = the least that covers EVERY planting week = %s"
	      % f"{cint(c.get('tc')):,}")
	print("                    with the cut diverted %s weeks; pool peaks %s"
	      % (c.get("divert_weeks"), f"{cint(c.get('peak_pool')):,}"))
	print("                    covers %s of %s weeks; cutting weeks per generation %s"
	      % (c.get("weeks_met"), c.get("weeks"),
	         "/".join(str(g["cutting_weeks"]) for g in (c.get("generations") or []))))
