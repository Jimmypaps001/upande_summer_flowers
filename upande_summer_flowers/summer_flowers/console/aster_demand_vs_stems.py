"""Every week: the stems the market asked for, and the stems the crop gives.

Supply is built from the motherstock plan -- cuttings off the pool, rooted three
weeks, then eight flushes over 111 weeks at 18.8 stems a plant. Demand is the
market demand on record for the variety and farm. Nothing is smoothed.
"""

import frappe
from frappe.utils import add_days, cint, flt, getdate


def main(plan="SFPP-2026-00061", cycles=1, weeks=0):
	from upande_summer_flowers.summer_flowers import propagation_solver as ps
	from upande_summer_flowers.summer_flowers.console.motherstock_table import (
		_round_up_to_area,
	)

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	demand_cut = ps.demand_by_sticking_week(p)
	peak, first_stick = max(demand_cut.values()), min(demand_cut)
	estab = cint(v.weeks_tc_to_first_cut())
	regen = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)
	per_area = cint(v.plants_per_bed) or 1000
	per_plant = flt(v.cuttings_per_plant_required) or 1.0
	to_plant = cint(v.sticking_to_planting_weeks)
	offsets = v.flush_offsets()

	buy = _round_up_to_area(peak / float(cycles + 1), per_area)
	first_cut = add_days(getdate(first_stick), -7 * cycles * regen)
	line_end = add_days(first_cut, 7 * cint(v.motherstock_life_weeks))

	stems = {}
	week = getdate(first_cut)
	while week < line_end:
		standing = buy * (1 + sum(1 for c in range(1, cycles + 1)
		                          if week >= add_days(first_cut, 7 * c * regen)))
		cut = int(round(standing * (flt(v.cuttings_per_plant_per_week) or 1.0)))
		building = week < add_days(first_cut, 7 * cycles * regen)
		to_farm = 0 if building else min(cut, cint(demand_cut.get(week, 0)))
		if to_farm:
			planted = add_days(week, 7 * to_plant)
			plants = int(to_farm / per_plant)
			for off, per in offsets:
				d = add_days(planted, 7 * cint(off))
				stems[d] = stems.get(d, 0) + int(round(plants * flt(per)))
		week = add_days(week, 7)

	md = frappe.db.get_value("Summer Flower Market Demand",
	                         {"variety": p.variety, "farm": p.farm}, "name")
	demand = {}
	if md:
		for r in frappe.get_doc("Summer Flower Market Demand", md).demand_weeks:
			if r.week_start_date:
				demand[getdate(r.week_start_date)] = cint(r.demand_stems)

	# The demand record runs to 2050; the window that matters is the one this
	# planting actually touches, from its first stem back to the season it was
	# stuck in, and on to its last flush.
	start = min(list(stems) + [getdate(first_stick)])
	end = max(stems) if stems else max(demand)
	print("%s — %s at %s" % (plan, p.variety, p.farm))
	print("  %s plantlets, multiplied %s. plants flush at weeks %s after planting."
	      % (f"{buy:,}", cycles, ", ".join(str(cint(o)) for o, _s in offsets)))
	print("  %s .. %s\n" % (start, end))
	print("    wk  week of        demand     stems       +/-     cum demand      "
	      "cum stems       cum +/-")
	run_d = run_s = 0
	n = 0
	w = start
	limit = cint(weeks) or 10 ** 6
	while w <= end and n < limit:
		n += 1
		d = cint(demand.get(w, 0))
		s = cint(stems.get(w, 0))
		run_d += d
		run_s += s
		print("   %3d  %s %11s %9s %9s %14s %14s %13s"
		      % (n, w, f"{d:,}" if d else "·", f"{s:,}" if s else "·",
		         (f"{s - d:+,}" if (d or s) else "·"),
		         f"{run_d:,}", f"{run_s:,}",
		         f"{run_s - run_d:+,}"))
		w = add_days(w, 7)
	print("\n   over %d weeks: demand %s, stems %s, difference %s"
	      % (n, f"{run_d:,}", f"{run_s:,}",
	         f"{run_s - run_d:+,}"))
