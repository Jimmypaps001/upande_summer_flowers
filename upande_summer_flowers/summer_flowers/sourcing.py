# Copyright (c) 2026, James Kiruga and contributors
"""Where a plan's material comes from, how much of it, and when to order."""

import datetime
import math

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from upande_summer_flowers.summer_flowers import stages as _stages
from upande_summer_flowers.summer_flowers.crop_protocol import (
	ROUTE_END,
	ROUTE_ENTRY,
	TAKEN_STAGES as OWN_STAGES,
)


def _blocks(version):
	"""The version's route as [(stage row, [its steps])]."""
	blocks, current = [], None
	for row in (version.material_route or []):
		if (row.row_type or "Stage") == "Step":
			if current:
				current[1].append(row)
		else:
			current = (row, [])
			blocks.append(current)
	return blocks


def _survival(rows):
	"""What survives these rows. Losses compound; they do not add."""
	out = 1.0
	for r in rows:
		out *= 1.0 - flt(r.get("loss_pct")) / 100.0
	return out


def journey(version, from_stage):
	"""What it takes to turn one unit of `from_stage` into plants in the ground."""
	blocks = _blocks(version)
	names = [s.stage for s, _st in blocks]
	if from_stage not in names:
		return None
	start = names.index(from_stage)

	# Only this stage's own chain. A route can be bought into more than one way --
	end = next((i for i in range(start + 1, len(blocks))
	            if blocks[i][0].stage in ROUTE_ENTRY), len(blocks))
	chain = blocks[start:end]

	weeks = 0
	plants_per_unit = 1.0
	standing = None
	for stage, steps in chain:
		if cint(stage.get("is_standing")):
			# A pool is not a step on a conveyor. Carrying its multiplication into
			standing = stage.stage
		plants_per_unit *= _survival([stage])
		# A stage's yield is what it hands on when material LEAVES it, so it applies
		if stage.stage != ROUTE_END:
			# A one-off lift IS a conveyor, which is why the exclusion above does
			lift = flt(stage.get("returns_per_plant") or 0)
			plants_per_unit *= lift if lift > 0 else flt(stage.yields_per_unit or 1)
			plants_per_unit *= _survival(steps)
			weeks += sum(cint(s.weeks) for s in steps) if steps else cint(stage.weeks)
	return {
		"stage": from_stage,
		"weeks_to_ground": weeks,
		"standing": standing,
		# Only meaningful on a route that has no pool in it. Where there is one, the
		"plants_per_unit": plants_per_unit,
		"lead_weeks": cint(blocks[start][0].lead_weeks),
		"rate": flt(blocks[start][0].rate),
	}




@frappe.whitelist()
def route_plan(version):
	"""What the protocol already says about how this crop's material is got."""
	v = (version if hasattr(version, "material_route")
	     else frappe.get_cached_doc("Crop Protocol Version", version))
	blocks = _blocks(v)
	if not blocks:
		return {"has_route": False, "method": None, "entry_stage": None,
		        "buyable": [], "propagates": False, "in_house": [],
		        "reason": _("This protocol has no material route, so nothing about "
		                    "how the material is got can be read off it.")}

	names = [st.stage for st, _s in blocks]
	buyable = [st.stage for st, _s in blocks if cint(st.is_purchase)]
	# A route that marks nothing bought still starts somewhere. If that somewhere is
	entry = buyable[0] if buyable else None
	first = names[0]
	unmarked = not buyable and first not in OWN_STAGES

	start = names.index(entry) if entry else 0
	in_house = [n for n in names[start + 1:] if n != ROUTE_END]
	propagates = bool(in_house)

	if entry:
		method = "Purchase"
		if propagates:
			reason = _("{0} says the material is bought as {1} and then raised here "
			           "through {2}. Both happen: the {1} is ordered, and what it "
			           "becomes is propagated on the farm.").format(
				v.name, entry, ", ".join(in_house))
		else:
			reason = _("{0} says the material is bought as {1} and planted as it "
			           "arrives. Nothing is propagated here.").format(v.name, entry)
	elif unmarked:
		method = None
		reason = _("{0} starts at {1}, which is bought -- but no stage on the route "
		           "is ticked as the one it is bought at. Tick it on the protocol, "
		           "or the order has no stage, no lead time and no price."
		           ).format(v.name, first)
	else:
		method = "Propagate"
		reason = _("{0} starts at {1}, which the farm takes off its own crop. "
		           "Nothing is bought; it is all raised here through {2}."
		           ).format(v.name, first, ", ".join(in_house) or _("to planting"))

	return {"has_route": True, "method": method, "entry_stage": entry,
	        "buyable": buyable, "propagates": propagates, "in_house": in_house,
	        "first_stage": first, "unmarked": unmarked, "reason": reason,
	        # Every way in, with its share and its own lead time. A crop bought one
	        "ways_in": entry_ways(v)}


def entry_ways(version):
	"""Each chain of the route that buys something: stage, share, lead, what follows."""
	from upande_summer_flowers.summer_flowers.crop_protocol import (
		ROUTE_END, chain_share, route_chains,
	)

	# A Crop Protocol or a Crop Protocol Version, whichever is handed over: the
	v = version
	if isinstance(version, str):
		v = frappe.get_cached_doc("Crop Protocol Version", version)
	chains = route_chains(v)
	out = []
	for chain in chains:
		rows = [r for r, _st in chain]
		entry = next((r for r in rows if cint(r.get("is_purchase"))), None)
		if not entry:
			continue
		after = [r.stage for r in rows[rows.index(entry) + 1:] if r.stage != ROUTE_END]
		share = chain_share(chain)
		out.append({
			"stage": entry.stage,
			# One way in takes the whole order whatever the box says.
			"share_pct": share if len(chains) > 1 else 100.0,
			"lead_weeks": cint(entry.get("lead_weeks")),
			"weeks_to_ground": sum(cint(r.get("weeks")) for r in rows[rows.index(entry):]),
			"rate": flt(entry.get("rate")),
			"item": entry.get("item"),
			"in_house": after,
			"propagates": bool(after),
		})
	return out


def standing_stage(version, from_stage=None):
	"""The stage on this route that is established once and cut from, if any."""
	blocks = _blocks(version)
	names = [s.stage for s, _st in blocks]
	start = names.index(from_stage) if from_stage in names else 0
	for stage, _steps in blocks[start:]:
		if cint(stage.get("is_standing")):
			return stage
	return None


def own_stock(version, farm, variety, on_date, exclude=None):
	"""What the farm's own standing crop can hand over on `on_date`."""
	from upande_summer_flowers.summer_flowers.doctype.planting_calendar \
		.planting_calendar import standing_plantings

	rows = [r for r in (standing_plantings(farm, variety, on_date) or [])
	        if not exclude or r.get("name") not in exclude]
	from_plantings = sum(cint(r.get("plants_expected_standing") or r.get("plants"))
	                     for r in rows)
	# And what the blocks themselves say is growing. Two records of one crop:
	from_register = cint(frappe.db.sql("""
		select ifnull(sum(gv.plants), 0) from `tabGreenhouse Variety` gv
		join tabBlock b on b.name = gv.parent
		where gv.parenttype = 'Block' and gv.variety = %s and b.farm = %s
	""", (variety, farm))[0][0])
	plants = max(from_plantings, from_register)
	stage = next((st for st, _steps in _blocks(version)
	              if (st.get("stage") or "") in OWN_STAGES), None)
	per_plant = flt(stage.get("returns_per_plant")) if stage else 0.0
	per_plant = per_plant if per_plant > 0 else 1.0
	# And where the difference comes from when there is not enough standing. The
	own_stage = stage.stage if stage else None
	buy_stage = _stages.bought_as(own_stage) if own_stage else None
	# The stage's usual lead, and failing that the one this crop's protocol
	buy_lead = cint(_stages.info(buy_stage).get("default_lead_weeks")) if buy_stage else 0
	if buy_stage and not buy_lead:
		buy_lead = cint(version.get("supplier_lead_weeks"))
	return {
		"stage": own_stage,
		"buy_stage": buy_stage,
		"buy_lead_weeks": buy_lead,
		"plantings": len(rows),
		"plants_standing": plants,
		"from_plantings": from_plantings,
		"from_block_register": from_register,
		"basis": ("the blocks' own register" if from_register > from_plantings
		          else "the planting calendar"),
		"per_plant": per_plant,
		"units_available": int(plants * per_plant),
		"as_of": str(on_date),
	}


def standing_pool_capacity(version, on_date):
	"""Cuttings the motherstock already standing can give in the week of `on_date`."""
	if not on_date:
		return 0, []
	from upande_summer_flowers.summer_flowers.lifecycle_sim import ramp_ratio

	per_week = flt(version.cuttings_per_plant_per_week) or 1.0
	ramp = version.ramp_ratios() or [1.0]
	monday = getdate(on_date)
	total, used = 0, []
	# Submitted only, and not expired -- the same rule the propagation plan uses.
	for b in frappe.get_all("Summer Flower Motherstock Batch",
	                        filters={"variety": version.variety, "farm": version.farm,
	                                 "docstatus": 1,
	                                 "batch_status": ["!=", "Expired"]},
	                        fields=["name", "mother_plants", "first_sticking_date",
	                                "expiry_date"]):
		if not (b.mother_plants and b.first_sticking_date):
			continue
		start = getdate(b.first_sticking_date)
		if monday < start:
			continue
		if b.expiry_date and monday > getdate(b.expiry_date):
			continue
		cap = int(round(cint(b.mother_plants) * per_week
		                * ramp_ratio((monday - start).days // 7, ramp)))
		if cap:
			total += cap
			used.append({"batch": b.name, "mother_plants": cint(b.mother_plants),
			             "cuttings_this_week": cap})
	return total, used


def requirement(version, from_stage, plants, weekly_plants=None, peak_date=None,
                cycles=None, units_override=None):
	"""What to buy at `from_stage`, and in what shape, to grow `plants` plants."""
	j = journey(version, from_stage)
	if not j:
		return None
	stage = standing_stage(version, from_stage)
	if not stage:
		per_unit = flt(j["plants_per_unit"])
		return dict(j, kind="per_cohort", standing_stage=None, multiplies=False,
		            units=(int(math.ceil(plants / per_unit)) if (plants and per_unit)
		                   else 0),
		            basis=_("{0} plants at {1:.3f} plants per unit bought")
		                  .format(plants, per_unit))

	# The pool's own arithmetic, which is the protocol's and not a second opinion
	weekly = cint(weekly_plants or 0)
	per_week = flt(stage.get("yields_per_week"))
	# The protocol's cycle count unless the reader is trying another. Cycles are the
	cycles = cint(cycles) if cycles not in (None, "") else cint(
		version.max_multiplication_cycles)
	cuttings = cint(version.cuttings_for_plants(weekly)) if weekly else 0
	# Net off the pool the farm already stands. Sizing the order against the gross
	standing, from_batches = standing_pool_capacity(version, peak_date)
	short = max(0, cuttings - standing)
	mothers = int(math.ceil(short / per_week)) if (short and per_week) else 0
	calculated = int(math.ceil(version.tc_plants_for(mothers, cycles))) if mothers else 0
	# A figure typed by a person is the order; the calculation becomes advice about
	units = cint(units_override) if cint(units_override or 0) > 0 else calculated
	blocked = None
	if weekly and not per_week:
		# Yields nothing per week and the route still says it multiplies: the answer
		blocked = _(
			"{0} stands and is cut from, but the protocol does not say how many "
			"cuttings one {1} gives in a week. Without that the order cannot be "
			"sized, and sizing it as if each planting needed its own {0} would "
			"over-buy many times over."
		).format(stage.stage, stage.stage.lower())
	netting = (_(" {0} of those already come off {1} motherstock batch(es) standing, "
	             "so only {2} has to be raised.")
	           .format(f"{standing:,}", len(from_batches), f"{short:,}")
	           if standing else "")
	return dict(j, kind="standing", standing_stage=stage.stage, multiplies=True,
	            units=units, pool=mothers, weekly_draw=cuttings,
	            weekly_plants=weekly, yields_per_week=per_week, cycles=cycles,
	            blocked=blocked, standing_capacity=standing,
	            net_cuttings=short, from_batches=from_batches,
	            calculated_units=calculated,
	            overridden=bool(cint(units_override or 0) > 0
	                            and cint(units_override) != calculated),
	            basis=_("{0} plants stuck in the busiest week needs {1} cuttings.{2} "
	                    "That is {3} mother plants, raised from {4} TC plants at {5} "
	                    "multiplication cycles.")
	                  .format(f"{weekly:,}", f"{cuttings:,}", netting, f"{mothers:,}",
	                          f"{units:,}", cycles))


@frappe.whitelist()
def entry_options(version):
	"""Every stage this crop's material could be bought at, priced and dated."""
	v = frappe.get_cached_doc("Crop Protocol Version", version)
	out = []
	for stage, _steps in _blocks(v):
		if not cint(stage.is_purchase):
			continue
		j = journey(v, stage.stage)
		if not j:
			continue
		j["notice_weeks"] = j["weeks_to_ground"] + j["lead_weeks"]
		out.append(j)
	return out


@frappe.whitelist()
def order_schedule(plan, stage=None):
	"""What to buy and when, for every planting in a plan."""
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	options = entry_options(v.name)
	if not options:
		return {"plan": p.name, "stage": None, "rows": [], "totals": {},
		        "options": [], "note": _("This crop's route has no stage marked as "
		                                 "bought, so nothing about it can be ordered.")}
	chosen = next((o for o in options if o["stage"] == stage), options[0])
	if not chosen["plants_per_unit"]:
		return {"plan": p.name, "stage": chosen["stage"], "rows": [], "totals": {},
		        "options": options,
		        "note": _("Nothing survives the route from {0} as it is filled in, so "
		                  "no order can be sized from it.").format(chosen["stage"])}

	today = getdate()
	rows = []
	for b in p.plan_blocks:
		if not b.is_new_planting or not b.planting_date:
			continue
		plant_on = getdate(b.planting_date)
		units = int(round(cint(b.plants) / chosen["plants_per_unit"] + 0.4999))
		order_by = plant_on - datetime.timedelta(
			weeks=chosen["weeks_to_ground"] + chosen["lead_weeks"])
		rows.append({
			"row": b.name,
			"block": b.block,
			"beds": cint(b.beds),
			"plants": cint(b.plants),
			"planting_date": str(plant_on),
			"planting_year": b.planting_year,
			"planting_week": b.planting_week,
			"units": units,
			"cost": units * chosen["rate"],
			"order_by": str(order_by),
			# An order that had to be placed before today cannot be placed. Saying so
			"late": order_by < today,
			"days_left": (order_by - today).days,
		})
	rows.sort(key=lambda r: r["order_by"])
	late = [r for r in rows if r["late"]]
	return {
		"plan": p.name, "variety": p.variety, "farm": p.farm,
		"stage": chosen["stage"], "chosen": chosen, "options": options,
		"rows": rows,
		"totals": {
			"plantings": len(rows),
			"plants": sum(r["plants"] for r in rows),
			"units": sum(r["units"] for r in rows),
			"cost": sum(r["cost"] for r in rows),
			"late": len(late),
			"first_order": rows[0]["order_by"] if rows else None,
			"last_order": rows[-1]["order_by"] if rows else None,
			"notice_weeks": chosen["weeks_to_ground"] + chosen["lead_weeks"],
		},
	}


def report(plan, stage=None):
	"""Readable order_schedule(), for the console."""
	r = order_schedule(plan, stage)
	if r.get("note"):
		print(r["note"]); return r
	print("%s -- %s at %s" % (r["plan"], r["variety"], r["farm"]))
	print("\nwhere the material could be bought:")
	for o in r["options"]:
		print("   %-18s %2d wks to ground + %2d wks supplier lead = %2d weeks' notice; "
		      "1 unit -> %.2f plants" % (o["stage"], o["weeks_to_ground"],
		                                 o["lead_weeks"], o["notice_weeks"],
		                                 o["plants_per_unit"]))
	print("\nbuying at %s:" % r["stage"])
	print("   %-12s %-8s %10s %10s  %s" % ("order by", "plant wk", "plants", "units", ""))
	for x in r["rows"][:24]:
		print("   %-12s %-8s %10s %10s  %s" % (
			x["order_by"], "%s-W%02d" % (x["planting_year"], x["planting_week"] or 0),
			f"{x['plants']:,}", f"{x['units']:,}",
			"LATE -- that order date has passed" if x["late"] else ""))
	t = r["totals"]
	print("\n   %d plantings, %s plants, %s units to buy, %d weeks' notice needed"
	      % (t["plantings"], f"{t['plants']:,}", f"{t['units']:,}", t["notice_weeks"]))
	print("   ordering runs %s to %s; %d already late"
	      % (t["first_order"], t["last_order"], t["late"]))
	return r


@frappe.whitelist()
def harvest_chain(plan, stage=None):
	"""One row per planting, read the way a grower reads it: backwards from the cut."""
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	sched = order_schedule(plan, stage)
	orders = {r["row"]: r for r in sched.get("rows", [])}
	# The stage actually used, not the one asked for: called with nothing, the
	stage = sched.get("stage")
	first_off = cint(v.first_harvest_offset_weeks)
	life = cint(v.total_weeks_in_ground)
	turn = cint(v.turnaround_weeks)
	offsets = v.flush_offsets()
	last_off = max((o for o, _s in offsets), default=first_off)
	stick = cint(v.sticking_to_planting_weeks)

	rows = []
	for b in p.plan_blocks:
		if not b.planting_date:
			continue
		plant_on = getdate(b.planting_date)
		o = orders.get(b.name) or {}
		wk = lambda d: "%s-W%02d" % (d.isocalendar()[0], d.isocalendar()[1])
		uproot = plant_on + datetime.timedelta(weeks=life)
		rows.append({
			"row": b.name,
			"new": bool(b.is_new_planting),
			"block": b.block,
			"beds": cint(b.beds),
			"plants": cint(b.plants),
			"order_by": o.get("order_by"),
			"order_units": o.get("units"),
			"order_late": o.get("late"),
			"start_prop": str(plant_on - datetime.timedelta(weeks=stick)),
			"plant": str(plant_on),
			"plant_week": wk(plant_on),
			"first_cut": str(plant_on + datetime.timedelta(weeks=first_off)),
			"first_cut_week": wk(plant_on + datetime.timedelta(weeks=first_off)),
			"last_cut": str(plant_on + datetime.timedelta(weeks=last_off)),
			"uproot": str(uproot),
			"bed_free": str(uproot + datetime.timedelta(weeks=turn)),
			"harvests": len(offsets),
			"stems": int(round(sum(s for _o, s in offsets) * cint(b.plants))),
		})
	rows.sort(key=lambda r: r["plant"])
	return {
		"plan": p.name, "variety": p.variety, "farm": p.farm,
		"cycle": v.growing_cycle, "stage": stage,
		"protocol": {"weeks_to_first_cut": first_off, "harvests": len(offsets),
		             "weeks_in_ground": life, "turnaround_weeks": turn,
		             "sticking_weeks": stick},
		"rows": rows,
	}
