# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The cohort solver's properties, on crops of each kind.

	PYTHONPATH=/path/to/apps/upande_summer_flowers python3 \\
		upande_summer_flowers/summer_flowers/console/check_cohort_solver.py

No site and no frappe: the solver is arithmetic, and arithmetic should be
testable without a database behind it.
"""

import sys
import time

from upande_summer_flowers.summer_flowers.cohort_solver import (
	plan_cohorts, weekly_production,
)

RESULTS = []

# Plants are whole and a part-plant rounds up, so a week can finish a stem or
# two the far side of the band. The band is a planning tolerance in stems, not a
# promise about the last one, and the solver already leaves room for the
# rounding it can predict; this is what is left over.
SLIVER = 0.05


def check(name, ok, detail=""):
	RESULTS.append((name, bool(ok), detail))
	print(f"  {'PASS' if ok else '**FAIL**':<10} {name}   {detail}")


def band_report(rates, demand, cohorts, standing=None):
	prod = weekly_production(rates, cohorts, len(demand))
	if standing:
		prod = [p + s for p, s in zip(prod, standing)]
	worst_over = worst_short = 0.0
	for p, d in zip(prod, demand):
		if not d:
			continue
		v = (p - d) / d * 100
		worst_over, worst_short = max(worst_over, v), min(worst_short, v)
	return prod, worst_over, worst_short


def main():
	# ---- a crop cut once: every week stands alone, so the old answer was right
	rates = [12.0]
	demand = [0, 0, 1200, 0, 0, 2400, 0]
	coh, _r = plan_cohorts(rates, demand, band=0.10)
	prod, over, short = band_report(rates, demand, coh)
	check("cut once: each week sized on its own",
	      prod[2] >= 1080 and prod[5] >= 2160 and over <= 10 + SLIVER,
	      f"weeks {prod}")

	# ---- three flushes, flat demand
	rates = [1.0, 0, 0, 2.0, 0, 0, 1.5]
	demand = [1000] * 20
	coh, _r = plan_cohorts(rates, demand, band=0.10)
	prod, over, short = band_report(rates, demand, coh)
	check("flushes: every week inside the band",
	      over <= 10 + SLIVER and short >= -10 - SLIVER,
	      f"{short:+.1f}% to {over:+.1f}%, {len(coh)} cohorts")
	check("flushes: the season is covered",
	      sum(prod) >= sum(demand), f"{sum(prod):,} of {sum(demand):,}")

	# ---- a continuous crop whose curve is weakest in its first week, which is
	#      the shape that broke the old planner
	rates = [0.085, 0.134, 0.459, 0.099, 0.099, 0.099, 0.064, 0.092] * 6
	demand = [120000] * 40
	coh, _r = plan_cohorts(rates, demand, band=0.10)
	prod, over, short = band_report(rates, demand, coh)
	plants = sum(coh.values())
	check("continuous: every week inside the band",
	      over <= 10 + SLIVER and short >= -10 - SLIVER,
	      f"{short:+.1f}% to {over:+.1f}%")
	naive = sum(demand) / rates[0]  # what sizing on the first week would buy
	check("continuous: far fewer plants than sizing on week one",
	      plants < naive / 2, f"{plants:,} against {naive:,.0f}")

	# ---- standing crop is netted off, not planted for twice
	standing = [100000] * 40
	coh2, _r2 = plan_cohorts(rates, demand, standing=standing, band=0.10)
	plants2 = sum(coh2.values())
	check("standing crop reduces what is planted",
	      plants2 < plants, f"{plants2:,} against {plants:,} with bare ground")
	_p, over2, short2 = band_report(rates, demand, coh2, standing)
	check("standing crop: still inside the band",
	      over2 <= 10 + SLIVER and short2 >= -10 - SLIVER, f"{short2:+.1f}% to {over2:+.1f}%")

	# ---- a week nobody asked for is neither required nor forbidden
	demand3 = [0] * 10 + [50000] * 20 + [0] * 10
	coh3, _r3 = plan_cohorts(rates, demand3, band=0.10)
	prod3, over3, short3 = band_report(rates, demand3, coh3)
	check("weeks with no demand are not capped",
	      over3 <= 10 + SLIVER and short3 >= -10 - SLIVER and sum(prod3[:10]) >= 0,
	      f"{short3:+.1f}% to {over3:+.1f}%")

	# ---- a three-year horizon still answers, and quickly
	demand4 = [120000] * 156
	t0 = time.time()
	coh4, _r4 = plan_cohorts(rates, demand4, band=0.10)
	el = time.time() - t0
	_p4, over4, short4 = band_report(rates, demand4, coh4)
	check("three-year horizon stays inside the band",
	      over4 <= 10 + SLIVER and short4 >= -10 - SLIVER, f"{short4:+.1f}% to {over4:+.1f}%")
	check("three-year horizon solves in reasonable time", el < 30, f"{el:.1f}s")

	# ---- a planting quantum coarser than the band itself. The band cannot hold,
	#      and the solver has to say so rather than quietly abandoning it.
	coarse, rep = plan_cohorts([0.2, 0.5, 0.4], [26000] * 30, band=0.10,
	                           rounding_plants=300)
	check("a coarse planting quantum is still answered",
	      sum(coarse.values()) > 0, f"{sum(coarse.values()):,} plants")
	fine, rep2 = plan_cohorts([0.2, 0.5, 0.4], [26000] * 30, band=0.10,
	                          rounding_plants=1)
	_p5, over5, short5 = band_report([0.2, 0.5, 0.4], [26000] * 30, fine)
	check("a plant-sized quantum holds the band",
	      rep2["band_held"] and over5 <= 10 + SLIVER and short5 >= -10 - SLIVER,
	      f"{short5:+.1f}% to {over5:+.1f}%")

	# ---- a tighter band costs more plants, which is the trade it should make
	tight = sum(plan_cohorts(rates, demand, band=0.02)[0].values())
	check("a tighter band buys more plants", tight > plants,
	      f"+/-2% {tight:,} against +/-10% {plants:,}")

	bad = [n for n, ok, _d in RESULTS if not ok]
	print(f"\nRESULT: {len(RESULTS)} checks, {len(RESULTS) - len(bad)} passed, "
	      f"{len(bad)} failed")
	for n in bad:
		print("   FAILED:", n)
	return 1 if bad else 0


if __name__ == "__main__":
	sys.exit(main())
