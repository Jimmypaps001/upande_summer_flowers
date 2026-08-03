# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Motherstock lifecycle: TC order -> supplier lead -> establishment -> cuts -> expiry,
repeating, with propagation feedback.

Week by week:

* an active TC cycle supplies base capacity, ramping 25/50/75/100% over its first
  weeks rather than switching on at full rate;
* cuttings diverted to propagation in any week come back as their own motherstock
  sub-pool `ms_establishment_weeks` later, which then ramps and produces for a
  full life of its own;
* so the pool compounds continuously off weekly decisions, not in discrete rounds.

Diverting cuttings is the multiplication. Send everything to the field and
capacity is flat until the next TC order lands; divert some and capacity climbs
15 weeks later on top of whatever is already producing.

Nothing here touches the database, so one implementation serves a saved batch and
a what-if slider.
"""

import datetime

import frappe
from frappe.utils import flt, getdate

from upande_summer_flowers.summer_flowers.planning import iso_year_week

DEFAULT_RAMP = [0.25, 0.50, 0.75, 1.00]
# Guard against a misconfigured protocol producing an unbounded simulation.
MAX_WEEKS = 1040
MAX_POOLS = 4000


def parse_ramp(profile, ramp_weeks=None):
	"""'25,50,75,100' -> [0.25, 0.5, 0.75, 1.0]."""
	vals = []
	for part in str(profile or "").split(","):
		part = part.strip()
		if not part:
			continue
		try:
			vals.append(flt(part) / 100)
		except Exception:
			continue
	if not vals:
		vals = list(DEFAULT_RAMP)
	if ramp_weeks and len(vals) < int(ramp_weeks):
		vals += [vals[-1]] * (int(ramp_weeks) - len(vals))
	return vals


def params_from_version(version, overrides=None):
	v = frappe.get_cached_doc("Crop Protocol Version", version)
	p = {
		"version": v.name,
		"variety": v.variety,
		"farm": v.farm,
		"supplier_lead_weeks": int(v.supplier_lead_weeks or 0),
		"weeks_on_tray": int(v.weeks_on_tray or 0),
		"weeks_on_pot": int(v.weeks_on_pot or 0),
		"ramp_weeks": int(v.ramp_weeks or 0),
		"ms_establishment_weeks": int(v.ms_establishment_weeks or 0),
		"ms_life_weeks": int(v.motherstock_life_weeks or 0),
		"hardening_weeks": int(v.hardening_weeks or 0),
		"weeks_to_pinch": int(v.weeks_to_pinch or 0),
		"flush_interval_weeks": int(v.flush_interval_weeks or 0),
		"cutting_to_harvest_weeks": int(v.cutting_to_harvest_weeks or 0),
		"cuttings_per_plant_per_week": flt(v.cuttings_per_plant_per_week or 1),
		"plants_per_pot": int(v.plants_per_pot or 1),
		"pots_per_sqm": int(v.pots_per_sqm or 1),
		"plants_per_sqm_bench": flt(v.plants_per_sqm_bench or 0),
		"plants_per_bed": int(v.plants_per_bed or 1),
		"sqm_gross_per_bed": flt(v.sqm_gross_per_bed or 0),
		"total_stems_per_plant_life": flt(v.total_stems_per_plant_life or 0),
		"ramp_profile": v.ramp_profile,
	}
	for k, val in (overrides or {}).items():
		if k in p and val not in (None, ""):
			p[k] = type(p[k])(val) if not isinstance(p[k], str) else val
	p["ramp"] = parse_ramp(p["ramp_profile"], p["ramp_weeks"])
	return p


def build_cycles(p, tc_qty, order_date, num_cycles):
	"""The repeating TC loop, each cycle timed off the previous one's expiry."""
	lead = p["supplier_lead_weeks"]
	estab = p["ms_establishment_weeks"]
	life = p["ms_life_weeks"]
	ramp_w = max(1, p["ramp_weeks"])

	cycles = []
	sw = 0
	order = getdate(order_date)
	for i in range(int(num_cycles)):
		arrive_sw = sw + lead
		first_cut_sw = arrive_sw + estab
		expiry_sw = first_cut_sw + life
		c = {
			"cycle": i + 1,
			"tc_qty": int(tc_qty),
			"order_sw": sw,
			"arrive_sw": arrive_sw,
			"first_cut_sw": first_cut_sw,
			"full_sw": first_cut_sw + ramp_w - 1,
			"expiry_sw": expiry_sw,
			# Order the replacement one full lead-plus-establishment before this
			# motherstock dies, or the next one is not productive in time.
			"next_order_sw": expiry_sw - (lead + estab),
			"first_harvest_sw": first_cut_sw + p["cutting_to_harvest_weeks"],
			"order_date": order,
			"arrive_date": order + datetime.timedelta(weeks=lead),
			"tray_end_date": order + datetime.timedelta(weeks=lead + p["weeks_on_tray"]),
			"pot_end_date": order + datetime.timedelta(
				weeks=lead + p["weeks_on_tray"] + p["weeks_on_pot"]),
			"first_cut_date": order + datetime.timedelta(weeks=lead + estab),
			"full_date": order + datetime.timedelta(weeks=lead + estab + ramp_w - 1),
			"expiry_date": order + datetime.timedelta(weeks=lead + estab + life),
			"first_harvest_date": order + datetime.timedelta(
				weeks=lead + estab + p["cutting_to_harvest_weeks"]),
		}
		c["next_order_date"] = c["expiry_date"] - datetime.timedelta(weeks=lead + estab)
		cycles.append(c)
		order = c["next_order_date"]
		sw = expiry_sw
	return cycles


def ramp_ratio(week_in_pool, ramp):
	if week_in_pool < 0:
		return 0.0
	return ramp[week_in_pool] if week_in_pool < len(ramp) else ramp[-1]


def simulate(p, tc_qty, order_date, num_cycles=4, farm_overrides=None,
             default_to_prop_pct=0.0, extra_weeks=None, max_bench_sqm=None):
	"""Walk every week forward, letting diverted cuttings build new pools.

	`farm_overrides` is {sim_week: plants_to_field}; anything not overridden falls
	back to `default_to_prop_pct` diverted.

	`max_bench_sqm` is the physical ceiling on the pool and matters more than it
	looks: because a new pool's own output is also eligible for diversion, a
	standing divert percentage compounds without bound and runs to absurd numbers
	within a couple of years. Bench space is what actually stops it, so once the
	bench is full the diversion is trimmed to what fits and the week is flagged.
	"""
	farm_overrides = {int(k): int(v) for k, v in (farm_overrides or {}).items()}
	cycles = build_cycles(p, tc_qty, order_date, num_cycles)
	base = getdate(order_date)
	ramp = p["ramp"]
	estab = p["ms_establishment_weeks"]
	life = p["ms_life_weeks"]
	per_plant = p["cuttings_per_plant_per_week"]
	cut2harv = p["cutting_to_harvest_weeks"]

	max_plants = None
	if max_bench_sqm not in (None, "", 0) and p.get("plants_per_sqm_bench"):
		max_plants = int(flt(max_bench_sqm) * p["plants_per_sqm_bench"])

	last_sw = cycles[-1]["expiry_sw"] if cycles else 0
	horizon = min(MAX_WEEKS, (extra_weeks if extra_weeks is not None
	                          else last_sw + estab + len(ramp) + 12))

	events = {}

	def ev(sw, msg):
		if 0 <= sw <= horizon:
			events.setdefault(sw, []).append(msg)

	for c in cycles:
		n = c["cycle"]
		ev(c["order_sw"], f"TC order #{n} ({c['tc_qty']:,} plantlets)")
		ev(c["arrive_sw"], f"TC #{n} arrives")
		ev(c["arrive_sw"] + p["weeks_on_tray"], "Tray ends, pot stage begins")
		ev(c["arrive_sw"] + p["weeks_on_tray"] + p["weeks_on_pot"],
		   "Pot ends, ramp to full capacity begins")
		ev(c["first_cut_sw"], f"MS{n} first cut")
		ev(c["full_sw"], f"MS{n} at full capacity")
		ev(c["first_harvest_sw"], f"First harvest from MS{n} cuttings")
		ev(c["next_order_sw"], f"Order TC #{n + 1} now, or MS{n + 1} will be late")
		ev(c["expiry_sw"], f"MS{n} expires after {life} weeks of cutting")

	prop_pools = []      # {"start_sw", "plants", "src_sw"}
	rows = []
	truncated = False

	for sw in range(0, horizon + 1):
		date = base + datetime.timedelta(weeks=sw)
		year, week = iso_year_week(date)

		# ---- base capacity from the active TC cycle
		base_cap = 0
		ramp_pct = 0.0
		phase = ""
		source = None
		cycle_no = 0
		for c in cycles:
			if c["first_cut_sw"] <= sw < c["expiry_sw"]:
				w = sw - c["first_cut_sw"]
				ramp_pct = ramp_ratio(w, ramp)
				base_cap = int(round(c["tc_qty"] * per_plant * ramp_pct))
				phase = (f"Ramp {w + 1} ({int(round(ramp_pct * 100))}%)"
				         if w < len(ramp) else "Full")
				source = f"MS{c['cycle']}"
				cycle_no = c["cycle"]
				break
		else:
			for c in cycles:
				if sw == c["order_sw"]:
					phase, cycle_no = "TC order", c["cycle"]
					break
				if c["order_sw"] < sw < c["arrive_sw"]:
					phase = f"Supplier lead {sw - c['order_sw']}/{p['supplier_lead_weeks']}w"
					cycle_no = c["cycle"]
					break
				if sw == c["arrive_sw"]:
					phase, cycle_no = "TC arrives", c["cycle"]
					break
				if c["arrive_sw"] < sw < c["first_cut_sw"]:
					e = sw - c["arrive_sw"]
					if e <= p["weeks_on_tray"]:
						phase = f"Tray {e}/{p['weeks_on_tray']}w"
					elif e <= p["weeks_on_tray"] + p["weeks_on_pot"]:
						phase = f"Pot {e - p['weeks_on_tray']}/{p['weeks_on_pot']}w"
					else:
						phase = "Establishing"
					cycle_no = c["cycle"]
					break

		# ---- capacity from propagation-derived sub-pools
		prop_cap = 0
		prop_detail = []
		for pool in prop_pools:
			w = sw - pool["start_sw"]
			if w < 0 or w >= life:
				continue
			pct = ramp_ratio(w, ramp)
			cap = int(round(pool["plants"] * per_plant * pct))
			if not cap:
				continue
			prop_cap += cap
			prop_detail.append({
				"plants": pool["plants"], "from_week": pool["src_sw"],
				"pct": int(round(pct * 100)), "capacity": cap,
			})

		total_cap = base_cap + prop_cap

		# ---- split this week's cuttings
		if total_cap > 0:
			if sw in farm_overrides:
				to_farm = max(0, min(farm_overrides[sw], total_cap))
			else:
				to_farm = int(round(total_cap * (1 - flt(default_to_prop_pct) / 100)))
			to_prop = total_cap - to_farm
		else:
			to_farm = to_prop = 0

		# ---- diverted cuttings become a pool of their own later
		bench_limited = False
		diverted_planted = 0
		if to_prop > 0 and sw <= last_sw and len(prop_pools) < MAX_POOLS:
			start = sw + estab
			room = to_prop
			if max_plants is not None:
				# What will already be standing the week this pool comes online.
				standing = _pool_plants(cycles, prop_pools, start, life)
				room = max(0, min(to_prop, max_plants - standing))
				if room < to_prop:
					bench_limited = True
			if room > 0:
				prop_pools.append({"start_sw": start, "plants": room, "src_sw": sw})
				diverted_planted = room
				ev(start, f"Prop-MS from week {sw} ready ({room:,} plants, ramp begins)")
			if bench_limited:
				ev(sw, f"Bench full — only {room:,} of {to_prop:,} diverted cuttings "
				       f"could be potted")
			# Cuttings with nowhere to go are wasted, not silently absorbed.
			wasted = to_prop - diverted_planted
		elif to_prop > 0 and len(prop_pools) >= MAX_POOLS:
			truncated = True
			wasted = to_prop
		else:
			wasted = 0

		harvest_sw = sw + cut2harv if to_farm > 0 else None
		rows.append({
			"sw": sw,
			"date": str(date),
			"year": year,
			"week_no": week,
			"label": f"{year}-W{week:02d}",
			"phase": phase,
			"source": source,
			"cycle": cycle_no,
			"ramp_pct": int(round(ramp_pct * 100)),
			"base_cap": base_cap,
			"prop_cap": prop_cap,
			"prop_detail": prop_detail,
			"prop_pool_count": len(prop_detail),
			"total_cap": total_cap,
			"to_farm": to_farm,
			"to_prop": to_prop,
			"to_prop_pct": int(round(to_prop / total_cap * 100)) if total_cap else 0,
			"harvest_sw": harvest_sw,
			"harvest_date": str(base + datetime.timedelta(weeks=harvest_sw))
			if harvest_sw is not None else None,
			"harvest_label": None,
			"bench_limited": bench_limited,
			"cuttings_potted": diverted_planted,
			"cuttings_wasted": wasted,
			"ms_plants": _pool_plants(cycles, prop_pools, sw, life),
			"events": events.get(sw, []),
		})

	for r in rows:
		if r["harvest_sw"] is not None:
			d = base + datetime.timedelta(weeks=r["harvest_sw"])
			y, w = iso_year_week(d)
			r["harvest_label"] = f"{y}-W{w:02d}"
		r["bench_sqm"] = round(
			r["ms_plants"] / p["plants_per_sqm_bench"], 1
		) if p["plants_per_sqm_bench"] else 0
		r["pots"] = -(-r["ms_plants"] // p["plants_per_pot"]) if p["plants_per_pot"] else 0

	peak = max(rows, key=lambda r: r["ms_plants"]) if rows else {}
	peak_cap = max(rows, key=lambda r: r["total_cap"]) if rows else {}
	to_farm_total = sum(r["to_farm"] for r in rows)
	to_prop_total = sum(r["to_prop"] for r in rows)

	return {
		"params": p,
		"tc_qty": int(tc_qty),
		"order_date": str(base),
		"cycles": [_jsonable(c) for c in cycles],
		"rows": rows,
		"horizon_weeks": horizon,
		"prop_pools": len(prop_pools),
		"truncated": truncated,
		"max_bench_sqm": flt(max_bench_sqm) if max_bench_sqm else None,
		"max_ms_plants": max_plants,
		"bench_limited_weeks": sum(1 for r in rows if r["bench_limited"]),
		"totals": {
			"cuttings_to_farm": to_farm_total,
			"cuttings_to_prop": to_prop_total,
			"cuttings_potted": sum(r["cuttings_potted"] for r in rows),
			"cuttings_wasted": sum(r["cuttings_wasted"] for r in rows),
			"peak_ms_plants": peak.get("ms_plants", 0),
			"peak_ms_week": peak.get("label"),
			"peak_bench_sqm": peak.get("bench_sqm", 0),
			"peak_pots": peak.get("pots", 0),
			"peak_capacity": peak_cap.get("total_cap", 0),
			"peak_capacity_week": peak_cap.get("label"),
			"beds_from_farm_cuttings": round(
				to_farm_total / p["plants_per_bed"], 1) if p["plants_per_bed"] else 0,
			"stems_from_farm_cuttings": int(round(
				to_farm_total * p["total_stems_per_plant_life"])),
		},
	}


def _pool_plants(cycles, prop_pools, sw, life):
	"""Mother plants standing this week, base cycle plus every live sub-pool."""
	total = 0
	for c in cycles:
		if c["first_cut_sw"] <= sw < c["expiry_sw"]:
			total += c["tc_qty"]
	for pool in prop_pools:
		if pool["start_sw"] <= sw < pool["start_sw"] + life:
			total += pool["plants"]
	return total


def _jsonable(c):
	return {k: (str(v) if isinstance(v, datetime.date) else v) for k, v in c.items()}
