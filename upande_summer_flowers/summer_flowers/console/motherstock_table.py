"""Buy TC off the peak week, and let the system say how many times to multiply.

The farm's rule, in its own words: buy the peak week's cuttings if you never
multiply; half of it if you multiply once; a third if twice; a quarter if three
times -- so the divisor is the number of GENERATIONS, which is cycles + 1, and
the answer is rounded up to a whole minimum planting area.

What the cycle count costs is time, and that is the part nobody should have to
work out. Each multiplication is one establishment of a diverted cutting (tray +
pot), so the pool is only full that much later, and the order has to go in that
much earlier to still meet the first sticking week.
"""

import math

import frappe
from frappe.utils import add_days, cint, flt, getdate

MAX_CYCLES = 5


def _round_up_to_area(plants, per_area):
	"""Up to a whole minimum planting area. You cannot buy a third of a bed."""
	if per_area <= 0:
		return int(plants)
	return int(math.ceil(plants / float(per_area)) * per_area)


def main(plan="SFPP-2026-00061"):
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	demand = ps.demand_by_sticking_week(p)
	peak = max(demand.values())
	peak_week = max(demand, key=lambda d: demand[d])
	first_stick = min(demand)
	estab = cint(v.weeks_tc_to_first_cut())
	regen = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)
	lead = cint(v.supplier_lead_weeks)
	per_area = cint(v.plants_per_bed) or 1000
	life = cint(v.motherstock_life_weeks)

	print("%s — %s at %s" % (plan, p.variety, p.farm))
	print("  season %s · %s plants over %d sticking weeks"
	      % (p.season, f"{sum(demand.values()):,}", len(demand)))
	print("  PEAK WEEK          %s  %s cuttings  <- this is what sizes the order"
	      % (peak_week, f"{peak:,}"))
	print("  first sticking     %s" % first_stick)
	print("  protocol           TC to first cut %sw · a diverted cutting becomes a "
	      "mother in %sw · line lives %sw" % (estab, regen, life))
	print("  minimum planting area %s m2 = %s plants" % (cint(v.min_planting_area_sqm),
	                                                     f"{per_area:,}"))

	print("\n" + "=" * 92)
	print("WHAT TO BUY, BY HOW MANY TIMES YOU MULTIPLY")
	print("=" * 92)
	print("  mult.  gens   peak/gens      buy    pool full   order by      makes "
	      "first      cut")
	print("  cycles                    (rounded)   after             sticking?    "
	      "  weeks")
	rows = []
	for c in range(0, MAX_CYCLES + 1):
		gens = c + 1
		raw = peak / float(gens)
		buy = _round_up_to_area(raw, per_area)
		weeks_to_full = estab + c * regen
		order_by = add_days(getdate(first_stick), -7 * (lead + weeks_to_full))
		# The line is cleared one life after its first cut, whatever generation it is.
		cut_weeks = max(0, life - c * regen)
		ok = cut_weeks > 0
		rows.append((c, gens, raw, buy, weeks_to_full, order_by, cut_weeks, ok))
		print("  %4d  %5d %11s %9s %9sw  %s  %-12s %6s"
		      % (c, gens, f"{raw:,.0f}", f"{buy:,}", weeks_to_full, order_by,
		         "yes" if ok else "no — line gone", cut_weeks))

	# The system's suggestion: the most multiplication that still leaves the pool a
	# real cutting life, because every extra cycle is plantlets saved.
	best = [r for r in rows if r[7] and r[6] >= len(demand)]
	pick = best[-1] if best else rows[0]
	print("\n  SUGGESTED: multiply %d time(s) — buy %s plantlets, order by %s."
	      % (pick[0], f"{pick[3]:,}", pick[5]))
	print("             %s fewer plantlets than not multiplying, and the pool still "
	      "has %d cutting weeks for %d sticking weeks."
	      % (f"{rows[0][3] - pick[3]:,}", pick[6], len(demand)))
	# Nothing returned: bench prints a return value as JSON and this holds dates
	# as dict keys, which it cannot serialise.
	globals()["_LAST"] = (rows, demand, peak, first_stick, estab, regen, per_area)


def weekly(plan="SFPP-2026-00061", cycles=1):
	"""Week by week: what is cut, what goes back, what goes to the farm.

	Nothing is sent to propagation "because it is propagation week". The pool has
	to reach the peak week's requirement and no more, so the diversion runs until
	the generation it is building is big enough, and after that the cut goes to the
	field -- as much as the field has asked for that week, and no more. A cutting
	the field has not asked for is left on the mother.
	"""
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	demand = ps.demand_by_sticking_week(p)
	peak = max(demand.values())
	first_stick = min(demand)
	estab = cint(v.weeks_tc_to_first_cut())
	regen = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)
	per_week = flt(v.cuttings_per_plant_per_week) or 1.0
	per_area = cint(v.plants_per_bed) or 1000
	life = cint(v.motherstock_life_weeks)

	buy = _round_up_to_area(peak / float(cycles + 1), per_area)
	order_by = add_days(getdate(first_stick),
	                    -7 * (cint(v.supplier_lead_weeks) + estab + cycles * regen))
	first_cut = add_days(order_by, 7 * (cint(v.supplier_lead_weeks) + estab))

	print("%s — %s" % (plan, p.variety))
	print("  buy %s plantlets, multiply %d time(s). order %s, first cut %s."
	      % (f"{buy:,}", cycles, order_by, first_cut))
	print("  the peak week needs %s cuttings; %s mothers x %s a week gets there."
	      % (f"{peak:,}", f"{peak:,}", per_week))

	# generations: the order, then one per multiplication, each `regen` weeks on
	gens = [{"n": 1, "mothers": buy, "from": first_cut}]
	for c in range(1, cycles + 1):
		gens.append({"n": c + 1, "mothers": buy,
		             "from": add_days(first_cut, 7 * c * regen)})
	line_end = add_days(first_cut, 7 * life)

	print("\n  wk  week of      standing     cut   ->prop   ->farm  wants  cum.farm  note")
	week = getdate(first_cut)
	n = 0
	run = 0
	short_total = 0
	while week < line_end:
		n += 1
		standing = sum(g["mothers"] for g in gens if getdate(g["from"]) <= week)
		cut = int(round(standing * per_week))
		wants = cint(demand.get(week, 0))
		# still building: the whole cut goes back until the last generation is on
		building = week < getdate(gens[-1]["from"])
		to_prop = cut if building else 0
		to_farm = 0 if building else min(cut, wants)
		note = ""
		if any(getdate(g["from"]) == week for g in gens):
			g = next(g for g in gens if getdate(g["from"]) == week)
			note = "generation %d starts cutting" % g["n"]
		if week == getdate(first_stick):
			note = (note + "; " if note else "") + "farm starts planting"
		if wants and to_farm < wants:
			note = (note + "; " if note else "") + "SHORT %s" % f"{wants - to_farm:,}"
		run += to_farm
		short_total += max(0, wants - to_farm)
		print("  %3d  %s %9s %7s %8s %8s %6s %9s  %s"
		      % (n, week, f"{standing:,}", f"{cut:,}",
		         f"{to_prop:,}" if to_prop else "·", f"{to_farm:,}" if to_farm else "·",
		         f"{wants:,}" if wants else "·", f"{run:,}", note))
		week = add_days(week, 7)
	print("\n  %d weeks from the first cut to the block being cleared on %s."
	      % (n, line_end))
	print("  delivered to the farm %s of the %s it asked for%s."
	      % (f"{run:,}", f"{sum(demand.values()):,}",
	         ", short %s" % f"{short_total:,}" if short_total else " — every week met"))
	idle = sum(1 for g in [0] for _ in [0])
