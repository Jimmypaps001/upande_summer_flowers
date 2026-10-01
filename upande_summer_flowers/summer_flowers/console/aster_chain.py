"""Cuttings to plants to stems, against the stems the market asked for.

One cutting becomes one plant (the losses on this protocol are zero), a plant is
stuck three weeks before it goes in the ground, and then it flushes eight times
over 111 weeks -- 18.8 stems in all. So the stems a week's cuttings produce do not
arrive for another 23 weeks, and keep arriving for two more years.
"""

import datetime

import frappe
from frappe.utils import add_days, cint, flt, getdate


def main(plan="SFPP-2026-00061", cycles=1):
	from upande_summer_flowers.summer_flowers import propagation_solver as ps
	from upande_summer_flowers.summer_flowers.console.motherstock_table import (
		_round_up_to_area,
	)

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	demand_cut = ps.demand_by_sticking_week(p)
	peak = max(demand_cut.values())
	first_stick = min(demand_cut)

	estab = cint(v.weeks_tc_to_first_cut())
	regen = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)
	per_area = cint(v.plants_per_bed) or 1000
	per_plant = flt(v.cuttings_per_plant_required) or 1.0
	to_plant = cint(v.sticking_to_planting_weeks)
	offsets = v.flush_offsets()
	life = cint(v.motherstock_life_weeks)

	buy = _round_up_to_area(peak / float(cycles + 1), per_area)
	order_by = add_days(getdate(first_stick),
	                    -7 * (cint(v.supplier_lead_weeks) + estab + cycles * regen))
	first_cut = add_days(order_by, 7 * (cint(v.supplier_lead_weeks) + estab))
	line_end = add_days(first_cut, 7 * life)

	# the stems the market asked for, week by week, for this plan's season
	md = frappe.db.get_value("Summer Flower Market Demand",
	                         {"variety": p.variety, "farm": p.farm}, "name")
	demand_stems = {}
	if md:
		for r in frappe.get_doc("Summer Flower Market Demand", md).demand_weeks:
			if r.week_start_date:
				demand_stems[getdate(r.week_start_date)] = cint(r.demand_stems)

	print("%s — %s at %s, season %s" % (plan, p.variety, p.farm, p.season))
	print("  buy %s plantlets, multiply %s. first cut %s, block cleared %s."
	      % (f"{buy:,}", cycles, first_cut, line_end))
	print("  1 cutting = %s plant · stuck %s weeks before planting · %d flushes, "
	      "%.1f stems a plant over %d weeks"
	      % (per_plant, to_plant, len(offsets), sum(s for _w, s in offsets),
	         offsets[-1][0] if offsets else 0))

	# walk the motherstock weeks, send what the field wants, and fan each
	# planting out into its flushes
	stems_by_week, plants_by_week, cuttings_by_week = {}, {}, {}
	week, n = getdate(first_cut), 0
	while week < line_end:
		n += 1
		standing = buy * (1 + sum(1 for c in range(1, cycles + 1)
		                          if week >= add_days(first_cut, 7 * c * regen)))
		cut = int(round(standing * (flt(v.cuttings_per_plant_per_week) or 1.0)))
		building = week < add_days(first_cut, 7 * cycles * regen)
		wants = cint(demand_cut.get(week, 0))
		to_farm = 0 if building else min(cut, wants)
		if to_farm:
			cuttings_by_week[week] = to_farm
			plants = int(to_farm / per_plant)
			planted = add_days(week, 7 * to_plant)
			plants_by_week[planted] = plants_by_week.get(planted, 0) + plants
			for off, per in offsets:
				d = add_days(planted, 7 * cint(off))
				stems_by_week[d] = stems_by_week.get(d, 0) + int(round(plants * flt(per)))
		week = add_days(week, 7)

	all_weeks = sorted(set(list(stems_by_week) + list(demand_stems)
	                       + list(plants_by_week)))
	season_start = min(min(plants_by_week) if plants_by_week else getdate(first_stick),
	                   getdate(first_stick))
	print("\n  wk  week of      cuttings    plants   stems in  demand/wk   cum stems   cum demand")
	run_s = run_d = 0
	shown = 0
	for d in all_weeks:
		if d < season_start:
			continue
		s_in = cint(stems_by_week.get(d, 0))
		dem = cint(demand_stems.get(d, 0))
		run_s += s_in
		run_d += dem
		# one line a week for two years is unreadable, so only the weeks where
		# something actually happens are printed
		if not (s_in or dem or plants_by_week.get(d)):
			continue
		shown += 1
		if shown > 70:
			continue
		print("  %3d  %s %9s %9s %10s %10s %11s %12s"
		      % (shown, d,
		         f"{cuttings_by_week.get(d, 0):,}" if cuttings_by_week.get(d) else "·",
		         f"{plants_by_week.get(d, 0):,}" if plants_by_week.get(d) else "·",
		         f"{s_in:,}" if s_in else "·", f"{dem:,}" if dem else "·",
		         f"{run_s:,}", f"{run_d:,}"))

	tot_c = sum(cuttings_by_week.values())
	tot_p = sum(plants_by_week.values())
	tot_s = sum(stems_by_week.values())
	print("\n  SUMMARY")
	print("    cuttings taken to the field   %14s" % f"{tot_c:,}")
	print("    plants that becomes           %14s" % f"{tot_p:,}")
	print("    stems those plants give       %14s   over %d weeks to %s"
	      % (f"{tot_s:,}", len(stems_by_week), max(stems_by_week) if stems_by_week else "-"))
	print("    the plan asked for            %14s stems" % f"{cint(p.total_demand_stems):,}")
	if cint(p.total_demand_stems):
		print("    so this covers                %13.1f%% of the plan"
		      % (tot_s * 100.0 / cint(p.total_demand_stems)))
	within = sum(n for d, n in stems_by_week.items()
	             if d in demand_stems or (demand_stems and min(demand_stems) <= d <= max(demand_stems)))
	print("    market demand on record       %14s stems" % f"{sum(demand_stems.values()):,}")
	print("    stems landing inside it       %14s" % f"{within:,}")
