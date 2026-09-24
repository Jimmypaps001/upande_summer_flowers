"""What the TC dialog will show, and what moves when one thing changes."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()


def _run():
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			tc_options,
		)

	plan = frappe.db.get_value("Summer Flower Production Plan",
	                           {"variety": "Aster Pink Flash", "farm": "Karen",
	                            "docstatus": 1}, "name", order_by="creation desc")
	if not plan:
		print("no submitted Aster plan")
		return
	o = tc_options(plan)
	if not o.get("standing"):
		print("not a standing route: %s" % o.get("reason"))
		return
	print("%s — %s at %s" % (o["plan"], o["variety"], o["farm"]))
	print("bought as %s, raised through %s" % (o["entry_stage"], o["standing_stage"]))
	print("\n  peak sticking week   %s" % o["peak_week"])
	print("  plants that week     %s" % f"{o['peak_plants']:,}")
	print("  cuttings per plant   %s" % o["cuttings_per_plant"])
	print("  cuttings that week   %s" % f"{o['weekly_draw']:,}")
	print("  already standing     %s" % f"{o['standing_capacity']:,}")
	print("  to raise             %s" % f"{o['net_cuttings']:,}")
	print("  cuttings per mother  %s a week" % o["cuttings_per_mother_per_week"])
	print("  mother plants        %s" % f"{o['pool']:,}")
	print("  TC to buy            %s   (protocol says %s cycles)"
	      % (f"{o['units']:,}", o["protocol_cycles"]))
	print("  order by             %s%s"
	      % (o["order_by"], "  ALREADY PASSED" if o["order_late"] else ""))

	print("\n  %-7s %-8s %-11s %-11s %-13s %s"
	      % ("cycles", "factor", "pool", "TC to buy", "order by", "cutting weeks"))
	for r in o["by_cycles"]:
		print("  %-7s %-8s %-11s %-11s %-13s %s%s"
		      % (r["cycles"], "x%s" % r["factor"], f"{r['pool']:,}",
		         f"{r['units']:,}", r["order_by"] or "—",
		         r["cutting_weeks"] if r["cutting_weeks"] is not None else "?",
		         "   <- protocol" if r["is_protocol"] else ""))

	print("\n  changing the cycles on the dialog:")
	for c in (1, 2, 4, 6):
		x = tc_options(plan, cycles=c)
		print("     %d cycle(s) -> TC %-9s order by %-12s %s cutting weeks"
		      % (c, f"{x['units']:,}", x["order_by"], x["cutting_weeks"]))

	print("\n  typing a quantity by hand (4 cycles):")
	for q in (None, 5000, 20000):
		x = tc_options(plan, cycles=4, tc_qty=q)
		print("     asked %-8s -> order %-9s  sum says %-9s  overridden %s"
		      % (q or "—", f"{x['units']:,}", f"{x['calculated']:,}", x["overridden"]))
