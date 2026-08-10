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
from frappe.utils import cint, flt, getdate

from upande_summer_flowers.summer_flowers.planning import iso_year_week

DEFAULT_RAMP = [0.25, 0.50, 0.75, 1.00]
# Guard against a misconfigured protocol producing an unbounded simulation.
MAX_WEEKS = 1040
MAX_POOLS = 4000
# A backstop on the derived generation count, not a modelling choice.
MAX_CYCLES = 40


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
		# Time to the FIRST cutting, which is one establishment -- not to the full
		# pool, which is one per build-up cycle. The two were the same field, so a
		# four-cycle protocol showed nothing coming off the bench for 72 weeks when
		# in fact it cuts from week 18 off a quarter-built pool.
		"tc_to_first_cut_weeks": int(v.weeks_tc_to_first_cut() or 0),
		# One build-up round: cuttings taken off the standing pool, established, and
		# added to it. The protocol's own establishment, so turning hardening off
		# there shortens the round here too.
		"stage_interval_weeks": int(v.weeks_tc_to_first_cut() or 0),
		# Plantlets are multiplied up before they become mother plants, which is what
		# the build-up cycles in the lead time are for. The simulator used to treat
		# one plantlet as one mother while still waiting out the build-up, so an
		# order sized by the Motherstock Batch produced a fifth of the capacity that
		# batch had sized it for.
		"multiplication_factor": v.multiplication_factor(),
		# Carried so a caller can see which build-up the numbers assume, and
		# override it without having to know how the factor is worked out.
		"build_up_cycles": cint(v.max_multiplication_cycles),
		# A cutting diverted to the propagation unit becomes a mother plant after
		# tray and pot, then ramps. Not ms_establishment_weeks, which folds the build-up
		# in and would wait it out before ramping again; and not the TC path either,
		# because a cutting off the farm is not multiplied up in a lab first.
		"cutting_to_mother_weeks": (cint(v.weeks_on_tray) + cint(v.weeks_on_pot)),
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
		"sqm_net_per_bed": flt(v.sqm_net_per_bed or 0),
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
	estab = p.get("tc_to_first_cut_weeks") or p["ms_establishment_weeks"]
	life = p["ms_life_weeks"]
	ramp_w = max(1, p["ramp_weeks"])

	factor = flt(p.get("multiplication_factor")) or 1.0
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
			# The mother plants this order becomes, after build-up.
			"ms_plants": int(round(int(tc_qty) * factor)),
			"multiplication_factor": factor,
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
		# The build-up, stage by stage. One order does not become its whole pool at
		# once: the plantlets establish and give the first tranche of mothers, those
		# are cut, the cuttings establish in their turn and give the second, and so
		# on. N cycles is N establishments and the pool is only full at the end of
		# the last one -- but every tranche before it is cutting the whole time,
		# which is where the weekly cuttings between first cut and full pool come
		# from. Modelling the pool as arriving whole hid all of them.
		bu = max(1, cint(p.get("build_up_cycles") or 0))
		gap = int(p.get("stage_interval_weeks") or estab)
		per_stage = (flt(factor) / bu) if bu else 1.0
		c["stages"] = []
		for k in range(bu):
			ready = first_cut_sw + k * gap
			c["stages"].append({
				"stage": k + 1,
				"label": "MS%d.%d" % (i + 1, k + 1) if bu > 1 else "MS%d" % (i + 1),
				"plants": int(round(int(tc_qty) * per_stage)),
				"ready_sw": ready,
				"full_sw": ready + ramp_w - 1,
				"expiry_sw": ready + life,
				"ready_date": order + datetime.timedelta(weeks=lead + estab + k * gap),
				"expiry_date": order + datetime.timedelta(
					weeks=lead + estab + k * gap + life),
			})
		c["pool_full_sw"] = c["stages"][-1]["full_sw"]
		c["pool_full_date"] = c["stages"][-1]["ready_date"] + datetime.timedelta(
			weeks=ramp_w - 1)
		# The cycle is done when its last tranche dies, not its first.
		c["expiry_sw"] = c["stages"][-1]["expiry_sw"]
		c["expiry_date"] = c["stages"][-1]["expiry_date"]
		c["next_order_sw"] = c["expiry_sw"] - (lead + gap * bu)
		c["next_order_date"] = c["expiry_date"] - datetime.timedelta(
			weeks=lead + gap * bu)
		cycles.append(c)
		# The next cycle starts at the week its own order date says it does. Setting
		# this to expiry_sw put the replacement order in at the moment the previous
		# motherstock died, so its first cut landed a full lead-plus-establishment
		# later -- 53 weeks with no cutting capacity at all, while next_order_date
		# said the opposite. The dates and the week indices have to agree, or the
		# renewal that the schedule promises never happens in the simulation.
		order = c["next_order_date"]
		sw = c["next_order_sw"]
	return cycles


def cycles_for_horizon(p, tc_qty, order_date, horizon_weeks):
	"""How many TC generations the horizon actually needs.

	The count used to be picked from a dropdown, which is the wrong question: a
	generation exists because the one before it expires and something has to
	replace it. Renewal is already chained -- each cycle carries the week the next
	order must go in -- so the count falls out of how far ahead you are looking.
	Diverted cuttings add generations on top of these, and those are counted from
	the pools the simulation actually creates rather than guessed at here.
	"""
	horizon = max(1, int(horizon_weeks or 0))
	n = 1
	while n < MAX_CYCLES:
		cycles = build_cycles(p, tc_qty, order_date, n)
		# Enough when the last generation is still cutting at the horizon.
		if cycles[-1]["expiry_sw"] >= horizon:
			return n
		n += 1
	return MAX_CYCLES


def ramp_ratio(week_in_pool, ramp):
	if week_in_pool < 0:
		return 0.0
	return ramp[week_in_pool] if week_in_pool < len(ramp) else ramp[-1]


def simulate(p, tc_qty, order_date, num_cycles=None, farm_overrides=None,
             default_to_prop_pct=0.0, extra_weeks=None, max_bench_sqm=None,
             horizon_weeks=None):
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
	# Derived unless a caller insists, so the answer to "how many cycles" comes
	# from the period being planned instead of from a control on a page.
	if not num_cycles:
		num_cycles = cycles_for_horizon(p, tc_qty, order_date,
		                                horizon_weeks or extra_weeks or 156)
	cycles = build_cycles(p, tc_qty, order_date, int(num_cycles))
	base = getdate(order_date)
	ramp = p["ramp"]
	# Establishment for a DIVERTED CUTTING, which is the only thing this figure is
	# used for below. The TC cycles have their own, longer path in build_cycles.
	estab = p.get("cutting_to_mother_weeks") or p["ms_establishment_weeks"]
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
		# Each build-up tranche announces itself, so the week a new one lands is
		# visible rather than showing up as capacity that rose for no stated reason.
		for st in c["stages"]:
			if st["stage"] > 1:
				ev(st["ready_sw"],
				   f"{st['label']} arrives — {st['plants']:,} more mother plants, "
				   f"build-up {st['stage']} of {len(c['stages'])}")
			ev(st["expiry_sw"], f"{st['label']} expires")
		if len(c["stages"]) > 1:
			ev(c["pool_full_sw"],
			   f"MS{n} pool complete — {c['ms_plants']:,} mother plants after "
			   f"{len(c['stages'])} build-up cycles")
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
		stage_detail = []
		for c in cycles:
			if c["first_cut_sw"] <= sw < c["expiry_sw"]:
				# Every tranche of this order that is alive this week, each ramping
				# from its own arrival. Summing them is what makes the pool climb in
				# steps instead of appearing whole.
				for st in c["stages"]:
					w = sw - st["ready_sw"]
					if w < 0 or w >= life:
						continue
					pct = ramp_ratio(w, ramp)
					cap = int(round(st["plants"] * per_plant * pct))
					if not cap:
						continue
					base_cap += cap
					stage_detail.append({
						"stage": st["stage"], "label": st["label"],
						"plants": st["plants"], "pct": int(round(pct * 100)),
						"capacity": cap, "arrived_this_week": w == 0,
					})
				if not stage_detail:
					continue
				live = len(stage_detail)
				ramp_pct = max(d["pct"] for d in stage_detail) / 100.0
				newest = min(d["stage"] for d in stage_detail
				             if d["pct"] < 100) if any(
					d["pct"] < 100 for d in stage_detail) else None
				phase = ("Build-up stage %d of %d (%d%%)"
				         % (newest, len(c["stages"]), int(round(ramp_pct * 100)))
				         if newest else "Full")
				source = (", ".join(d["label"] for d in stage_detail)
				          if live > 1 else stage_detail[0]["label"])
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
			# Which build-up tranches are cutting this week, and whether one of them
			# landed in it. The schedule colours a tranche's arrival week off this.
			"stage_detail": stage_detail,
			"stage_arrived": next((d["label"] for d in stage_detail
			                       if d["arrived_this_week"] and d["stage"] > 1), None),
			"stages_live": len(stage_detail),
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

	# How many build-up rounds the motherstock life actually leaves room for.
	_gap = int(p.get("stage_interval_weeks") or p.get("ms_establishment_weeks") or 0)
	stages_ordered = max(1, cint(p.get("build_up_cycles") or 0))
	stages_that_fit = (int(p["ms_life_weeks"] // _gap) + 1) if _gap else stages_ordered

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
		# The pools themselves, not just how many. What was diverted in which week,
		# when the protocol's establishment brings it back, and what it then cuts --
		# which is the whole answer to "if I send half to propagation, when does it
		# join production".
		"prop_pool_rows": [{
			"src_sw": x["src_sw"],
			"src_date": str(base + datetime.timedelta(weeks=x["src_sw"])),
			"plants": x["plants"],
			"ready_sw": x["start_sw"],
			"ready_date": str(base + datetime.timedelta(weeks=x["start_sw"])),
			"full_sw": x["start_sw"] + len(ramp) - 1,
			"full_date": str(base + datetime.timedelta(
				weeks=x["start_sw"] + len(ramp) - 1)),
			"ends_sw": x["start_sw"] + life,
			"ends_date": str(base + datetime.timedelta(weeks=x["start_sw"] + life)),
			"weekly_capacity": int(round(x["plants"] * per_plant)),
			"establishment_weeks": x["start_sw"] - x["src_sw"],
		} for x in prop_pools],
		"num_cycles": len(cycles),
		"cycles_derived": True,
		# A diverted batch that matures is a generation in its own right: it cuts,
		# and what it cuts can be diverted again. Counted from the pools the run
		# created rather than asserted up front.
		"generations": len(cycles) + len(prop_pools),
		"truncated": truncated,
		# A build-up cannot stack more tranches than a motherstock lives to see. With
		# an 18-week round and a 52-week life only three are ever standing together:
		# the fourth lands two weeks after the first has died, so a fourth cycle buys
		# nothing and an order sized by dividing the requirement by four arrives a
		# quarter short. Stated here because it is a property of the protocol, not of
		# any one order, and it is invisible in the totals.
		"stages_that_fit": stages_that_fit,
		"stages_ordered": stages_ordered,
		"build_up_capped": bool(stages_ordered > stages_that_fit),
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
	"""Mother plants standing this week, base cycle plus every live sub-pool.

	Summed tranche by tranche, each from its own arrival to its own expiry. Counting
	the order's whole pool from the first cut said 4,000 plants were standing in a
	week when the first thousand were already dead and the last had not arrived --
	and it is the figure the bench area, the pot count and the pool chart are all
	drawn from, so the bench read full when it was not.
	"""
	total = 0
	for c in cycles:
		stages = c.get("stages")
		if stages:
			for st in stages:
				if st["ready_sw"] <= sw < st["expiry_sw"]:
					total += st["plants"]
		elif c["first_cut_sw"] <= sw < c["expiry_sw"]:
			total += c["ms_plants"]
	for pool in prop_pools:
		if pool["start_sw"] <= sw < pool["start_sw"] + life:
			total += pool["plants"]
	return total


def _jsonable(c):
	return {k: (str(v) if isinstance(v, datetime.date) else v) for k, v in c.items()}
