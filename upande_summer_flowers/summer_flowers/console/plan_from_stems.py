"""Work a stem demand back to the plantlets, and forward again to production.

Given stems wanted week by week, this finds the plantings that produce them, the
cuttings those plantings need, the motherstock that cuts them and the tissue
culture that starts it -- then plays the whole thing forward to say what is
actually produced against what was asked for.

The chain, on Aster Pink Flash's protocol:
  TC -> 15 weeks -> first cutting -> (multiply: 11 weeks a generation)
  cutting -> 3 weeks rooting -> planted
  planted -> flushes at weeks 20, 33, 46, 59, 72, 85, 98, 111
             at 2.7, 2.6, 2.4, 2.3, 2.2, 2.2, 2.2, 2.2 stems a plant
"""

import datetime
import math

import frappe
from frappe.utils import add_days, cint, flt, getdate

# the demand as given: (year, iso week, stems)
DEMAND = (
	[(2027, w, 72600) for w in range(27, 35)]
	+ [(2027, w, 108900) for w in range(35, 44)]
	+ [(2027, w, 72600) for w in range(44, 48)]
	+ [(2027, w, 36300) for w in range(48, 53)]
)


def _monday(year, week):
	return datetime.date.fromisocalendar(year, week, 1)


def main(version="Aster Pink Flash-Karen-v17", cycles=1):
	v = frappe.get_cached_doc("Crop Protocol Version", version)
	offsets = v.flush_offsets()
	to_plant = cint(v.sticking_to_planting_weeks)
	estab = cint(v.weeks_tc_to_first_cut())
	regen = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)
	per_area = cint(v.plants_per_bed) or 1000
	per_plant = flt(v.cuttings_per_plant_required) or 1.0
	per_mother = flt(v.cuttings_per_plant_per_week) or 1.0
	life = cint(v.motherstock_life_weeks)

	demand = {_monday(y, w): s for y, w, s in DEMAND}
	weeks = sorted(demand)
	print("DEMAND: %s stems over %d weeks, %s .. %s, peak %s"
	      % (f"{sum(demand.values()):,}", len(weeks), weeks[0], weeks[-1],
	         f"{max(demand.values()):,}"))
	print("PROTOCOL %s: %d flushes, %.1f stems a plant, first at week %d"
	      % (version, len(offsets), sum(s for _w, s in offsets), offsets[0][0]))

	# ---- 1. plantings that produce it, earliest flush first
	supply = {}
	plantings = {}
	for w in weeks:
		short = demand[w] - cint(supply.get(w, 0))
		if short <= 0:
			continue
		plant_on = add_days(w, -7 * cint(offsets[0][0]))
		plants = int(math.ceil(short / flt(offsets[0][1])))
		plants = int(math.ceil(plants / float(per_area)) * per_area)
		plantings[plant_on] = plantings.get(plant_on, 0) + plants
		for off, per in offsets:
			d = add_days(plant_on, 7 * cint(off))
			supply[d] = cint(supply.get(d, 0)) + int(round(plants * flt(per)))

	print("\nPLANTINGS NEEDED (%d weeks, %s plants)"
	      % (len(plantings), f"{sum(plantings.values()):,}"))
	print("   plant on     plants   stuck on    cuttings")
	sticking = {}
	for d in sorted(plantings):
		stick = add_days(d, -7 * to_plant)
		cut = int(math.ceil(plantings[d] * per_plant))
		sticking[stick] = sticking.get(stick, 0) + cut
		print("   %s %9s   %s %9s" % (d, f"{plantings[d]:,}", stick, f"{cut:,}"))

	peak = max(sticking.values())
	peak_week = max(sticking, key=lambda d: sticking[d])
	first_stick = min(sticking)
	print("\nCUTTINGS: peak %s in the week of %s; first sticking %s"
	      % (f"{peak:,}", peak_week, first_stick))

	# ---- 2. what to buy, by how many times it is multiplied
	print("\nWHAT TO BUY")
	print("   mult.  gens   peak/gens      buy    order by      cut weeks")
	options = []
	for c in range(0, 5):
		gens = c + 1
		raw = peak / float(gens) / per_mother
		buy = int(math.ceil(raw / float(per_area)) * per_area)
		order = add_days(first_stick, -7 * (cint(v.supplier_lead_weeks)
		                                    + estab + c * regen))
		cut_weeks = max(0, life - c * regen)
		options.append((c, buy, order, cut_weeks))
		print("   %4d  %5d %11s %9s  %s %8s%s"
		      % (c, gens, f"{raw:,.0f}", f"{buy:,}", order, cut_weeks,
		         "   <- suggested" if c == cycles else ""))

	c, buy, order, cut_weeks = options[cycles]
	first_cut = add_days(order, 7 * (cint(v.supplier_lead_weeks) + estab))
	print("\nSUGGESTED: buy %s plantlets, multiply %d, order by %s, first cut %s."
	      % (f"{buy:,}", cycles, order, first_cut))
	globals()["_P"] = (v, demand, supply, plantings, sticking, buy, cycles,
	                   order, first_cut, offsets)


def full(version="Aster Pink Flash-Karen-v17", cycles=1):
	"""The whole plan: buy, build the pool, supply the field, and what comes out."""
	main(version, cycles)
	(v, demand, _s, plantings, sticking, buy, cycles, order, first_cut,
	 offsets) = globals()["_P"]
	regen = cint(v.weeks_on_tray) + cint(v.weeks_on_pot)
	per_mother = flt(v.cuttings_per_plant_per_week) or 1.0
	to_plant = cint(v.sticking_to_planting_weeks)
	life = cint(v.motherstock_life_weeks)
	line_end = add_days(first_cut, 7 * life)

	print("\n" + "=" * 94)
	print("PROPAGATION PLAN — what the motherstock does each week")
	print("=" * 94)
	print("   wk  week of     mothers      cut   ->propagation   ->field   field wants  note")
	week, n = getdate(first_cut), 0
	taken, wasted = 0, 0
	while week < line_end:
		n += 1
		standing = buy * (1 + sum(1 for c in range(1, cycles + 1)
		                          if week >= add_days(first_cut, 7 * c * regen)))
		cut = int(round(standing * per_mother))
		building = week < add_days(first_cut, 7 * cycles * regen)
		wants = cint(sticking.get(week, 0))
		to_prop = cut if building else 0
		to_field = 0 if building else min(cut, wants)
		taken += to_field
		if not building and wants == 0:
			wasted += cut
		note = ""
		if week == add_days(first_cut, 7 * cycles * regen):
			note = "generation %d cutting; field starts" % (cycles + 1)
		elif n == 1:
			note = "generation 1 first cut"
		if wants and to_field < wants:
			note = (note + "; " if note else "") + "SHORT %s" % f"{wants - to_field:,}"
		if n <= 30 or wants or note:
			print("   %3d  %s %9s %8s %13s %9s %12s  %s"
			      % (n, week, f"{standing:,}", f"{cut:,}",
			         f"{to_prop:,}" if to_prop else "·",
			         f"{to_field:,}" if to_field else "·",
			         f"{wants:,}" if wants else "·", note))
		week = add_days(week, 7)
	print("\n   pool %s mothers · %s cuttings to the field · %s left on the mother"
	      % (f"{buy * (cycles + 1):,}", f"{taken:,}", f"{wasted:,}"))

	print("\n" + "=" * 94)
	print("EXPECTED PRODUCTION — stems against the demand that was asked for")
	print("=" * 94)
	supply = {}
	for d, plants in plantings.items():
		for off, per in offsets:
			k = add_days(d, 7 * cint(off))
			supply[k] = cint(supply.get(k, 0)) + int(round(plants * flt(per)))
	span = sorted(set(list(demand) + [d for d in supply if d <= max(demand)]))
	run_d = run_s = 0
	print("   wk  week of        demand      stems        +/-      cum demand   cum stems")
	for i, d in enumerate(span, 1):
		dd, ss = cint(demand.get(d, 0)), cint(supply.get(d, 0))
		run_d += dd
		run_s += ss
		print("   %3d  %s %11s %10s %10s %13s %11s"
		      % (i, d, f"{dd:,}" if dd else "·", f"{ss:,}" if ss else "·",
		         f"{ss - dd:+,}" if (dd or ss) else "·", f"{run_d:,}", f"{run_s:,}"))
	later = sum(n for k, n in supply.items() if k > max(demand))
	print("\n   over the 26 demand weeks: asked %s, produced %s (%s)"
	      % (f"{run_d:,}", f"{run_s:,}", f"{run_s - run_d:+,}"))
	print("   and %s more stems land after the last demand week, to %s"
	      % (f"{later:,}", max(supply)))
