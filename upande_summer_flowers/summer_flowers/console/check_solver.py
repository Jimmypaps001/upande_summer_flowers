"""What the propagation solver recommends, on real plans. Read-only."""

import frappe
from frappe.utils import cint, flt


def main(limit=4):
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	names = frappe.get_all("Summer Flower Production Plan",
	                       filters={"docstatus": ["<", 2]},
	                       fields=["name", "variety", "farm"],
	                       order_by="modified desc", limit_page_length=40)
	done = 0
	for row in names:
		if done >= cint(limit):
			break
		plan = frappe.get_doc("Summer Flower Production Plan", row.name)
		if not plan.get("protocol"):
			continue
		r = ps.recommend(plan)
		if not r.get("propagates"):
			continue
		done += 1
		print("\n" + "=" * 78)
		print("%s  --  %s at %s" % (plan.name, r["variety"], r["farm"]))
		print("=" * 78)
		if not r.get("solvable"):
			print("  not solvable: %s" % r.get("reason"))
			continue
		n, L = r["need"], r["limits"]
		print("  needs %s plants (%s cuttings) across %d weeks, %s .. %s"
		      % (f"{n['plants']:,}", f"{n['cuttings']:,}", n["weeks"],
		         n["first_sticking"], n["last_sticking"]))
		print("  peak week %s cuttings" % f"{n['peak_week_cuttings']:,}")
		print("  protocol: lead %sw + establishment %sw, life %sw, TC loss %s%%"
		      % (L["supplier_lead_weeks"], L["establishment_weeks"],
		         L["life_weeks"], L["tc_loss_pct"]))
		print("  last useful diversion: %s weeks of cutting" % L["last_divert_weeks"])
		print("  standing motherstock at first sticking: %s (shown, not netted)"
		      % f"{r['standing']['plants']:,}")

		print("\n  divert   TC     order by     covers   peak pool   to field    cost")
		for o in r["options"]:
			if not o.get("covers"):
				print("  %5dw       -  %-11s  blocked: %s"
				      % (o["divert_weeks"], str(o["order_by"]),
				         (o.get("blocked") or "")[:96]))
				continue
			print("  %5dw %7s  %-11s  %-7s %10s %10s  %9s%s"
			      % (o["divert_weeks"], f"{o['tc']:,}", str(o["order_by"]), "yes",
			         f"{o['peak_pool']:,}", f"{o['total_to_field']:,}",
			         f"{o['cost']:,.0f}",
			         "   ORDER DATE PASSED" if o["order_late"] else ""))
		rec = r.get("recommended")
		if rec:
			print("\n  RECOMMENDED: buy %s, divert %s weeks, order by %s%s"
			      % (f"{rec['tc']:,}", rec["divert_weeks"], rec["order_by"],
			         "  (already late)" if rec["order_late"] else ""))
			c = r["chosen"]
			print("    TC arrives %s, first cutting %s" % (c["arrive_date"], c["first_cut_date"]))
			print("    pool peaks %s at week %s, block cleared %s"
			      % (f"{c['peak_pool']:,}", c["peak_pool_week"], c["line_end_date"]))
			print("    weekly cover %d of %d weeks, %s cuttings to the field over the line"
			      % (c["weeks_met"], c["weeks"], f"{c['total_to_field']:,}"))
		for a in r.get("assumed") or []:
			print("    ! %s" % a)
