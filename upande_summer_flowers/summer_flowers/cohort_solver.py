# Copyright (c) 2026, James Kiruga and contributors

"""Sizing a season's plantings as one problem instead of one week at a time."""

import math

from upande_summer_flowers.summer_flowers.simplex import minimise

# Above this the tableau costs more than the answer is worth, so a long horizon
MAX_SOLVE_WEEKS = 70


def plan_cohorts(rates, demand, standing=None, band=0.10, season_floor=True,
                 earliest_start=None, rounding_plants=1, allowed_starts=None):
	"""How many plants to start in which week, for one variety at one farm.

	`allowed_starts` is the only weeks a cohort may start in, as offsets into
	the demand window. Material does not arrive on demand: a house comes on
	its own rhythm, and a week no house can deliver to is a week nothing can
	be planted in, however neatly it would have closed a gap. Given None, any
	week is open, which is the answer for a crop raised from our own pool."""
	rates = [float(r or 0) for r in rates]
	weeks = len(demand)
	if not rates or not weeks or max(rates) <= 0:
		return {}
	standing = [float(s) for s in (standing or [0] * weeks)]
	band = max(0.0, float(band))

	step = max(1.0, float(rounding_plants or 1))
	report = {"band_held": True, "slices": 0}
	allowed = None if allowed_starts is None else sorted({int(x) for x in allowed_starts})
	# Nowhere to plant at all. The caller is told, rather than handed an empty
	# programme that reads exactly like a season with no demand in it.
	if allowed is not None and not allowed:
		report["no_delivery_weeks"] = True
		return {}, report
	if weeks <= MAX_SOLVE_WEEKS:
		report["slices"] = 1
		return _solve(rates, demand, standing, band, season_floor, earliest_start,
		              step, report, allowed), report


	# A long horizon in slices. Each slice is solved against what is already
	out = {}
	have = list(standing)
	span = len(rates)
	for lo in range(0, weeks, MAX_SOLVE_WEEKS):
		hi = min(weeks, lo + MAX_SOLVE_WEEKS)
		far = min(weeks, hi + span)
		# Only the first slice may reach back before its own first week. A later
		back = (-(span - 1) if earliest_start is None else earliest_start) if lo == 0 else 0
		report["slices"] += 1
		# The slice counts its weeks from its own start, so the allowed weeks
		# have to be shifted with it or a late slice plants on the wrong beat.
		here = (None if allowed is None
		        else [x - lo for x in allowed if x - lo >= back])
		part = _solve(rates, demand[lo:far], have[lo:far], band, season_floor,
		              back, step, report, here, reach_from=lo, reach_upto=hi - lo)
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
           report=None, allowed_starts=None, reach_from=0, reach_upto=None):
	span, weeks = len(rates), len(demand)
	first = -(span - 1) if earliest_start is None else int(earliest_start)
	starts = list(range(first, weeks))
	if allowed_starts is not None:
		open_weeks = set(allowed_starts)
		starts = [s for s in starts if s in open_weeks]
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

	# Weeks nothing allowed to be planted can ever reach. Asking them to meet
	# their floor makes the whole season unsolvable, and the answer the plan
	# needs is not "plant nothing": it is "plant everything the open weeks can
	# carry, and here is the week that stays short". A supplier on twenty
	# weeks' lead does this to the opening of every plan drawn today.
	# Only weeks that would actually ASK something of a cohort: a week already
	# covered by what is standing needs nothing planted for it, so it is not
	# short however far away the next delivery is.
	out_of_reach = [w for w in range(weeks)
	                if float(demand[w]) * (1.0 - band) - standing[w] > 0
	                and not any(a[w])]
	if out_of_reach and report is not None:
		# Reported in the CALLER's week numbers, and only for the weeks this
		# slice is actually deciding: the tail a slice reads past its own end
		# belongs to the next one, which may well be able to reach it.
		mine = [w for w in out_of_reach
		        if reach_upto is None or w < reach_upto]
		if mine:
			report.setdefault("weeks_out_of_reach", [])
			seen = set(report["weeks_out_of_reach"])
			report["weeks_out_of_reach"] += [w + reach_from for w in mine
			                                 if w + reach_from not in seen]
			report["weeks_out_of_reach"].sort()

	# Plants are whole, and a cohort is rounded up again to something plantable
	def attempt(guess):
		rows, rhs = _rows(a, demand, standing, band, season_floor, weeks, n,
		                  sum(rates) * max(0.0, guess), skip=out_of_reach)
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
		if report is not None:
			report["band_held"] = False
		rows, rhs = _rows(a, demand, standing, band, season_floor, weeks, n, 0.0,
		                  ceilings=False, skip=out_of_reach)
		x = minimise([1.0] * n, rows, rhs) if rows else None
	if x is None:
		return {}

	# Plants are whole, and a part-plant rounds up: under-planting to save a
	return {s: int(math.ceil(p)) for s, p in zip(starts, x) if math.ceil(p) >= 1}


def _rows(a, demand, standing, band, season_floor, weeks, n, allowance,
          ceilings=True, skip=None):
	"""The band, week by week, as rows of `rows . x <= rhs`."""
	rows, rhs = [], []
	unreachable = set(skip or ())
	for w in range(weeks):
		d = float(demand[w])
		# A week no permitted cohort reaches cannot be held to the band. It is
		# left to come out as the deficit it is.
		if w in unreachable:
			continue
		if d <= 0:
			# Nothing is asked for in this week, so nothing is required of it and
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

	if season_floor:
		want = (sum(float(d) for w, d in enumerate(demand)
		            if w not in unreachable)
		        - sum(s for w, s in enumerate(standing)
		              if w not in unreachable))
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
