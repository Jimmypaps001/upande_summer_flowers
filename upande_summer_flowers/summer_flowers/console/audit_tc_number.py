"""Is 29,600 really the order? Check it against the demand, not the engine."""

import frappe
from frappe.utils import cint, flt


def main(plan="SFPP-2026-00061"):
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	demand = ps.demand_by_sticking_week(p)
	per_week = flt(v.cuttings_per_plant_per_week) or 1.0
	estab = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)

	print("DEMAND, week by week")
	tot = 0
	for d, n in demand.items():
		tot += n
		print("   %s  %8s" % (d, f"{n:,}"))
	print("   total %s over %d weeks, peak %s"
	      % (f"{tot:,}", len(demand), f"{max(demand.values()):,}"))

	print("\nWHAT A MOTHER IS WORTH")
	print("   cuttings per mother per week   %s" % per_week)
	print("   motherstock life               %s weeks" % cint(v.motherstock_life_weeks))
	print("   so one mother yields           %s cuttings over its life"
	      % f"{int(per_week * cint(v.motherstock_life_weeks)):,}")
	print("   cuttings needed / per mother   %s mothers would cover the TOTAL"
	      % f"{int(tot / (per_week * cint(v.motherstock_life_weeks))):,}")
	print("   but the PEAK week needs        %s mothers standing at once"
	      % f"{int(max(demand.values()) / per_week):,}")
	print("   a diverted cutting becomes a mother in %d weeks (tray %s + pot %s)"
	      % (estab, cint(v.weeks_on_tray), cint(v.weeks_on_pot)))

	print("\nIS 29,600 MINIMAL AT 4 WEEKS OF DIVERSION?")
	first = min(demand)
	for tc in (20000, 25000, 28000, 29000, 29500, 29600, 29700, 35000):
		r = ps.evaluate(p.protocol, tc, 4, demand, first, flt(v.tc_order_loss_pct))
		if not r:
			continue
		r.pop("sim", None)
		print("   %8s plantlets -> covers %s (%d/%d weeks, short %s), peak pool %s"
		      % (f"{tc:,}", "YES" if r["covers"] else "no ",
		         r["weeks_met"], r["weeks"], f"{r['shortfall']:,}",
		         f"{r['peak_pool']:,}"))

	print("\nWHAT IS BINDING")
	r = ps.schedule_for(p.protocol, 29600, 4, demand, first, flt(v.tc_order_loss_pct))
	tight = [w for w in r["weeks_table"]
	         if w["demand"] and w["cuttings_cut"] - w["demand"] < w["demand"] * 0.08]
	for w in tight[:6]:
		print("   wk %-3s %s  needs %-8s cut %-8s spare %s"
		      % (w["week_no"], w["week_start"], f"{w['demand']:,}",
		         f"{w['cuttings_cut']:,}",
		         f"{w['cuttings_cut'] - w['demand']:,}"))

	print("\nHOW BUSY IS THE POOL IT BUYS")
	rows = [w for w in r["weeks_table"] if w["mothers_standing"]]
	cut = sum(w["cuttings_cut"] for w in rows)
	used = sum(w["to_field"] for w in rows)
	idle = [w for w in rows if w["mothers_standing"] and not w["demand"]]
	print("   peak pool                 %s mothers" % f"{r['peak_pool']:,}")
	print("   cuttings it could take    %s over the line" % f"{cut:,}")
	print("   cuttings the field wants  %s" % f"{used:,}")
	print("   so it is used for         %.1f%% of what it can cut"
	      % (used * 100.0 / cut if cut else 0))
	print("   weeks with mothers but no demand at all: %d of %d"
	      % (len(idle), len(rows)))
	bench = flt(v.plants_per_sqm_bench)
	if bench:
		print("   bench for the peak pool   %s m2" % f"{r['peak_pool'] / bench:,.0f}")


def smoothing(plan="SFPP-2026-00061"):
	"""If the peak week were spread, what would the order be? Read-only."""
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	base = ps.demand_by_sticking_week(p)
	first = min(base)
	loss = flt(v.tc_order_loss_pct)
	total = sum(base.values())

	print("The order is set by one week. What if that week were spread?\n")
	print("   cap/wk   weeks used   plantlets   peak pool   used %")
	for cap in (37000, 30000, 25000, 20000, 15000, 12000):
		# Spill anything over the cap into the following weeks, keeping the total
		# and never starting earlier -- the field can stick a planting later, not
		# before the plan begins.
		days = sorted(base)
		spread, carry = {}, 0
		for i, d in enumerate(days):
			want = base[d] + carry
			take = min(want, cap)
			spread[d] = take
			carry = want - take
		# whatever is still carried goes on in weekly steps after the last week
		from frappe.utils import add_days
		d = days[-1]
		while carry > 0:
			d = add_days(d, 7)
			take = min(carry, cap)
			spread[d] = take
			carry -= take
		r = ps.least_tc_for(p.protocol, 4, spread, first, loss)
		if not r or not r.get("covers"):
			print("   %6s   %10s   %s" % (f"{cap:,}", len(spread),
			                              (r or {}).get("blocked", "no answer")[:60]))
			continue
		cut = r["peak_pool"]
		print("   %6s   %10s   %9s   %9s   %5.1f%%"
		      % (f"{cap:,}", len(spread), f"{r['tc']:,}", f"{cut:,}",
		         total * 100.0 / (r["total_to_field"] or 1)))
	print("\n   the season needs %s cuttings in total, and one mother yields %s"
	      % (f"{total:,}", int(flt(v.cuttings_per_plant_per_week)
	                           * cint(v.motherstock_life_weeks))))
	print("   so %s mothers would do it if the weeks were even."
	      % f"{int(total / (flt(v.cuttings_per_plant_per_week) * cint(v.motherstock_life_weeks))):,}")
