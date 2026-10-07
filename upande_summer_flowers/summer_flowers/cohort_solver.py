# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

"""Sizing a season's plantings as one problem instead of one week at a time.

The planner used to answer each short week on its own: divide the shortfall by
what a plant gives in its FIRST productive week, plant that many, fold the
result in and move on. For a crop that is cut once those are the same number.
For a crop that is cut for a year they are not, and that is where the plans went
wrong.

Eryngium gives 0.085 stems a plant in its first productive week, 0.459 at the
peak and 0.136 on average across the 52 weeks it is cut. A cohort sized on 0.085
is sized on the weakest point of its own curve, and from its third week it
delivers up to five times what it was planted for -- not once, every week, for a
year. SFPP-2026-00067 closed four weeks exactly and overshot the other
forty-eight by an average of 82%: 1.82 million plants for a season that a little
over a million covers.

Nothing was wrong with the arithmetic in that loop. What was wrong is the
question. A cohort is not a week's answer, it is a year's, and it has to be
sized against every week it will feed.

Two rules make the better question solvable:

  * A cohort may start producing BEFORE the plan's window. Its early, weak weeks
    then fall into the preceding season and its strong weeks land on the demand
    being planned. Without this rule the band below is simply unreachable: the
    first week of a cold start can only be served by a cohort at its weakest,
    and that one cohort then floods the rest of the year.
  * A week may sit anywhere inside a tolerance band of its demand rather than
    being driven to meet it exactly, while the season as a whole still covers
    the season.

Of every planting programme that satisfies those, this takes the one that plants
the fewest plants, because plants are what is bought.
"""

import math

from upande_summer_flowers.summer_flowers.simplex import minimise

# Above this the tableau costs more than the answer is worth, so a long horizon
# is solved a slice at a time, each slice seeing what the ones before it planted.
MAX_SOLVE_WEEKS = 70


def plan_cohorts(rates, demand, standing=None, band=0.10, season_floor=True,
                 earliest_start=None, rounding_plants=1):
	"""How many plants to start in which week, for one variety at one farm.

	`rates` is stems per plant for each week of a cohort's productive life, in
	order: rates[0] is its first productive week. `demand` and `standing` are
	weekly, across the plan's window. The answer is {start: plants}, where
	`start` is the window index of a cohort's first productive week and may be
	negative -- a cohort already cutting when the window opens.

	`band` is the tolerance either way: 0.10 lets a week land between 90% and
	110% of its demand. A week with no demand is not capped, because there is no
	percentage of nothing and stems grown then belong to another season's plan.

	`rounding_plants` is how much bigger than this answer a cohort may end up
	once it is rounded to something plantable -- a whole plant here, but the
	caller sizes it to whole beds or to the protocol's minimum area, and that
	rounding lands in the same weeks. Room is left for it, so the band holds for
	the plan that is finally written rather than for the one solved here.
	"""
	rates = [float(r or 0) for r in rates]
	weeks = len(demand)
	if not rates or not weeks or max(rates) <= 0:
		return {}
	standing = [float(s) for s in (standing or [0] * weeks)]
	band = max(0.0, float(band))

	step = max(1.0, float(rounding_plants or 1))
	report = {"band_held": True, "slices": 0}
	if weeks <= MAX_SOLVE_WEEKS:
		report["slices"] = 1
		return _solve(rates, demand, standing, band, season_floor, earliest_start,
		              step, report), report

	# A long horizon in slices. Each slice is solved against what is already
	# standing plus everything the earlier slices planted, so a cohort is never
	# counted twice and a boundary is not a cold start.
	#
	# Each slice can SEE a cohort's whole life, not just the part inside it: it
	# is solved over its own weeks plus a full productive span beyond them, and
	# only the cohorts starting inside the slice are kept. Without that look
	# ahead a slice plants for its own last weeks and floods the first weeks of
	# the next one, which no later slice can undo -- a three-year plan came out
	# 73% over in the weeks either side of each boundary.
	out = {}
	have = list(standing)
	span = len(rates)
	for lo in range(0, weeks, MAX_SOLVE_WEEKS):
		hi = min(weeks, lo + MAX_SOLVE_WEEKS)
		far = min(weeks, hi + span)
		# Only the first slice may reach back before its own first week. A later
		# one that did would plant into weeks an earlier slice has already filled
		# to their ceiling -- and it cannot see those weeks to know it, which is
		# how a three-year plan ran 25% over in the middle of its second year.
		back = (-(span - 1) if earliest_start is None else earliest_start) if lo == 0 else 0
		report["slices"] += 1
		part = _solve(rates, demand[lo:far], have[lo:far], band, season_floor,
		              back, step, report)
		for s, plants in part.items():
			if s >= hi - lo:
				continue  # a later slice's to decide, with its own demand in view
			out[s + lo] = out.get(s + lo, 0) + plants
			for k, r in enumerate(rates):
				w = s + lo + k
				if 0 <= w < weeks:
					have[w] += plants * r
	return out, report


def _solve(rates, demand, standing, band, season_floor, earliest_start, step=1.0,
           report=None):
	span, weeks = len(rates), len(demand)
	first = -(span - 1) if earliest_start is None else int(earliest_start)
	starts = list(range(first, weeks))
	n = len(starts)
	if not n:
		return {}

	# a[w][j]: stems week w gets from one plant of cohort j
	a = [[0.0] * n for _ in range(weeks)]
	for j, s in enumerate(starts):
		for k, r in enumerate(rates):
			w = s + k
			if 0 <= w < weeks and r > 0:
				a[w][j] = r

	# Plants are whole, and a cohort is rounded up again to something plantable
	# -- the protocol's minimum area, or a whole bed. That rounding lands in the
	# same weeks as the cohort, so room is left under each ceiling for it.
	#
	# How much room is a guess, and a bad guess is expensive both ways. The
	# worst case -- every cohort rounded up by a full step -- is what a bed-sized
	# step makes of it: Dahlia plants 300 to a bed against a band of 2,600 stems,
	# and reserving 3,600 made the band unsatisfiable, so the solver abandoned
	# every ceiling and came back 90% over. So it is tried generously and then
	# less so, and the first allowance that can be met is the one used.
	# A smaller allowance is a weaker constraint, so if none at all is still
	# unsatisfiable nothing in between can help and there is no point looking.
	def attempt(guess):
		rows, rhs = _rows(a, demand, standing, band, season_floor, weeks, n,
		                  sum(rates) * max(0.0, guess))
		return minimise([1.0] * n, rows, rhs) if rows else None

	x = attempt(step)
	if x is None:
		bare = attempt(0.0)
		if bare is not None:
			for guess in (step / 2.0, step / 4.0, 1.0):
				if guess >= step:
					continue
				x = attempt(guess)
				if x is not None:
					break
			if x is None:
				x = bare

	if x is None:
		# No allowance makes the band reachable -- usually a week whose demand no
		# cohort can get to in time, or a planting quantum coarser than the band
		# itself. Cover the demand instead and let the plan report the overshoot.
		if report is not None:
			report["band_held"] = False
		rows, rhs = _rows(a, demand, standing, band, season_floor, weeks, n, 0.0,
		                  ceilings=False)
		x = minimise([1.0] * n, rows, rhs) if rows else None
	if x is None:
		return {}

	# Plants are whole, and a part-plant rounds up: under-planting to save a
	# fraction of a plant is not a saving anybody wanted.
	return {s: int(math.ceil(p)) for s, p in zip(starts, x) if math.ceil(p) >= 1}


def _rows(a, demand, standing, band, season_floor, weeks, n, allowance,
          ceilings=True):
	"""The band, week by week, as rows of `rows . x <= rhs`."""
	rows, rhs = [], []
	for w in range(weeks):
		d = float(demand[w])
		if d <= 0:
			# Nothing is asked for in this week, so nothing is required of it and
			# nothing is forbidden: what grows then is the next season's to sell.
			continue
		floor = d * (1.0 - band) - standing[w]
		if floor > 0:
			rows.append([-v for v in a[w]])
			rhs.append(-floor)
		if not ceilings:
			continue
		ceiling = d * (1.0 + band) - standing[w] - allowance
		if ceiling > 0:
			rows.append(list(a[w]))
			rhs.append(ceiling)
		# A week already over its ceiling from standing crop cannot be fixed by
		# planting, so it is left out rather than making the whole plan insoluble.

	if season_floor:
		want = sum(float(d) for d in demand) - sum(standing)
		if want > 0:
			total = [sum(a[w][j] for w in range(weeks)) for j in range(n)]
			rows.append([-v for v in total])
			rhs.append(-want)
	return rows, rhs


def weekly_production(rates, cohorts, weeks):
	"""What a solved programme puts in each week of the window."""
	out = [0] * weeks
	for start, plants in cohorts.items():
		for k, r in enumerate(rates):
			w = start + k
			if 0 <= w < weeks:
				out[w] += int(round(plants * r))
	return out
