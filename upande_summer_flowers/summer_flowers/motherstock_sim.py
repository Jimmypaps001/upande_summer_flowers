# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Motherstock build-up simulation: TC purchase -> multiplication -> field plants.

The model follows the grower's own description. A pool of productive mother plants
is split each round: some leave for the field, the rest are retained and yield new
plants, so the pool becomes `retained x (1 + factor)`. With factor 1 and nothing
sent to the field, the pool doubles every round.

    1000 -> 500 to field, 500 retained -> 500 + 500 = 1000
    1000 -> 0 to field,  1000 retained -> 1000 + 1000 = 2000

That compounds, which is not what the planning workbook assumed. Its formula,
`TC = mothers / (1 + cycles x factor)`, is linear: each round adds one cohort of
the original size. Four rounds gives 16x here against 5x there, so the two
disagree by roughly 3x on how many plantlets to buy. Both are reported.

Nothing here touches the database, so the same code serves a saved batch and a
what-if slider.
"""

import datetime
import math

import frappe
from frappe.utils import flt, getdate

from upande_summer_flowers.summer_flowers.planning import iso_year_week

# Parameters the simulation needs, with the field they come from on a
# Crop Protocol Version.
PARAM_FIELDS = (
	"weeks_on_tray",
	"hardening_weeks",
	"weeks_on_pot",
	"weeks_to_max_pc",
	"establishment_weeks",
	"cuttings_per_plant_per_week",
	"multiplication_factor_per_cycle",
	"cycle_time_weeks",
	"plants_per_pot",
	"pots_per_sqm",
	"plants_per_sqm_bench",
	"plants_per_bed",
	"sqm_gross_per_bed",
	"motherstock_life_weeks",
	"cuttings_per_plant_required",
	"cutting_reject_pct",
	"total_stems_per_plant_life",
)


def params_from_version(version, overrides=None):
	"""Plain dict of simulation parameters, with optional what-if overrides."""
	v = frappe.get_cached_doc("Crop Protocol Version", version)
	p = {f: v.get(f) for f in PARAM_FIELDS}
	p["version"] = v.name
	p["variety"] = v.variety
	p["farm"] = v.farm

	# A cutting is plantable once rooted and hardened; it only becomes a mother
	# capable of yielding the next round after the full establishment.
	p["weeks_to_field_ready"] = (v.weeks_on_tray or 0) + (v.hardening_weeks or 0)
	p["weeks_to_productive_ms"] = v.establishment_weeks or 0

	for k, val in (overrides or {}).items():
		if val is not None and k in p or k in ("weeks_to_field_ready",
		                                      "weeks_to_productive_ms"):
			p[k] = flt(val) if isinstance(p.get(k), float) else val
	return p


def simulate(
	tc_plants,
	tc_arrival_date,
	params,
	rounds=4,
	field_pct_per_round=None,
	horizon_weeks=160,
):
	"""Walk the pool forward round by round.

	`field_pct_per_round` is the share of the pool released to the field at each
	round; a single number applies to every round, a list gives per-round control.
	Returns the timeline, the round-by-round ledger and headline totals.
	"""
	tc_plants = int(tc_plants or 0)
	arrival = getdate(tc_arrival_date)
	rounds = int(rounds or 0)
	factor = flt(params.get("multiplication_factor_per_cycle") or 0)
	to_productive = int(params.get("weeks_to_productive_ms") or 0)
	to_field = int(params.get("weeks_to_field_ready") or 0)
	cycle = int(params.get("cycle_time_weeks") or 0)

	if isinstance(field_pct_per_round, (int, float)) or field_pct_per_round is None:
		pct = flt(field_pct_per_round if field_pct_per_round is not None else 0)
		field_pcts = [pct] * rounds
	else:
		field_pcts = [flt(x) for x in field_pct_per_round]
		field_pcts += [0.0] * max(0, rounds - len(field_pcts))

	# The TC cohort is only a usable mother pool after a full establishment.
	events = []          # (week_offset, kind, qty)
	pool_ready_week = to_productive
	events.append((0, "tc_received", tc_plants))
	events.append((pool_ready_week, "pool_productive", tc_plants))

	ledger = []
	pool = tc_plants
	cursor = pool_ready_week
	cumulative_field = 0

	for r in range(1, rounds + 1):
		released = int(round(pool * (field_pcts[r - 1] / 100)))
		retained = pool - released
		produced = int(round(retained * factor))

		# New plants are plantable sooner than they are able to mother the next
		# round, so the field release and the next round run off different clocks.
		field_week = cursor + cycle + to_field
		next_pool_week = cursor + cycle + to_productive

		cumulative_field += released
		if released:
			events.append((cursor, "to_field", released))
		if produced:
			events.append((next_pool_week, "ms_added", produced))

		ledger.append({
			"round": r,
			"at_week_offset": cursor,
			"pool_before": pool,
			"released_to_field": released,
			"retained": retained,
			"produced": produced,
			"pool_after": retained + produced,
			"pool_after_week_offset": next_pool_week,
			"field_ready_week_offset": field_week,
		})

		pool = retained + produced
		cursor = next_pool_week

	# ---- weekly timeline
	adds = {}
	for offset, kind, qty in events:
		if kind in ("pool_productive", "ms_added"):
			adds[offset] = adds.get(offset, 0) + qty
		elif kind == "to_field":
			adds.setdefault(offset, 0)
	releases = {}
	for offset, kind, qty in events:
		if kind == "to_field":
			releases[offset] = releases.get(offset, 0) + qty

	timeline = []
	running = 0
	field_total = 0
	for w in range(0, int(horizon_weeks) + 1):
		running += adds.get(w, 0)
		running -= releases.get(w, 0)
		field_total += releases.get(w, 0)
		d = arrival + datetime.timedelta(weeks=w)
		y, wk = iso_year_week(d)
		pots = math.ceil(running / (params.get("plants_per_pot") or 1)) if running else 0
		bench = (
			running / params["plants_per_sqm_bench"]
			if params.get("plants_per_sqm_bench") else 0
		)
		timeline.append({
			"week_offset": w,
			"date": str(d),
			"year": y,
			"week_no": wk,
			"label": f"{y}-W{wk:02d}",
			"ms_pool": running,
			"added": adds.get(w, 0),
			"released_to_field": releases.get(w, 0),
			"field_cumulative": field_total,
			"pots": pots,
			"bench_sqm": round(bench, 1),
			"weekly_cutting_capacity": int(
				running * flt(params.get("cuttings_per_plant_per_week") or 0)
			),
		})

	peak = max(timeline, key=lambda r: r["ms_pool"]) if timeline else {}
	linear_equivalent = tc_plants * (1 + rounds * factor)
	return {
		"tc_plants": tc_plants,
		"tc_arrival_date": str(arrival),
		"rounds": rounds,
		"factor": factor,
		"final_pool": pool,
		"multiplication_achieved": (pool / tc_plants) if tc_plants else 0,
		"workbook_linear_pool": int(linear_equivalent),
		"workbook_multiplication": (1 + rounds * factor),
		"field_plants_released": cumulative_field,
		"peak_pool": peak.get("ms_pool", 0),
		"peak_bench_sqm": peak.get("bench_sqm", 0),
		"peak_pots": peak.get("pots", 0),
		"peak_week": peak.get("label"),
		"pool_ready_week_offset": pool_ready_week,
		"ledger": ledger,
		"timeline": timeline,
	}


def tc_needed_for(target_pool, rounds, factor, field_pct_per_round=0):
	"""Smallest TC order that reaches `target_pool` after `rounds`.

	Solved forward rather than closed-form, because releasing plants to the field
	mid-way makes the growth path depend on rounding at each step.
	"""
	target_pool = int(target_pool or 0)
	if target_pool <= 0:
		return 0

	def pool_after(tc):
		pool = tc
		for _ in range(int(rounds or 0)):
			released = int(round(pool * (flt(field_pct_per_round) / 100)))
			retained = pool - released
			pool = retained + int(round(retained * flt(factor)))
		return pool

	lo, hi = 1, max(1, target_pool)
	if pool_after(hi) < target_pool:      # releasing to field can outpace growth
		hi = target_pool * 4 or 4
	while lo < hi:
		mid = (lo + hi) // 2
		if pool_after(mid) >= target_pool:
			hi = mid
		else:
			lo = mid + 1
	return lo


def rounds_that_fit(weeks_available, params):
	"""How many multiplication rounds fit before the plants are needed."""
	per_round = (
		int(params.get("cycle_time_weeks") or 0)
		+ int(params.get("weeks_to_productive_ms") or 0)
	)
	usable = int(weeks_available or 0) - int(params.get("weeks_to_productive_ms") or 0)
	if per_round <= 0 or usable < 0:
		return 0
	return max(0, usable // per_round)
