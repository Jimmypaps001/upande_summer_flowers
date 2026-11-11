# Copyright (c) 2026, James Kiruga and contributors
"""Who supplies the material, in how many cycles, and what that does to the plan.

A tissue culture house does not deliver a week's plants in that week. It takes
an order and hands it over in cycles -- "four lifts, the first at seven weeks,
the rest a week apart" -- so part of every consignment lands after the week it
was ordered for. That carry is not waste: it is next week's plants, already
bought. Buying as though every week stood alone orders the same plants twice.

This module answers, for a chosen set of suppliers and their stated cycles:
what has to be ordered each week once the carry is counted, when it lands, and
which planting weeks are still short. Nothing here is saved; it is for trying
suppliers against each other before anything is committed to.
"""

import datetime
import math

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

DEFAULT_FORM = "Tissue Culture"

# What a house offers when nothing else is known: four lifts a week apart,
# the bulk in the first. A supplier who works differently is edited on screen;
# this is only the shape to start the conversation from.
DEFAULT_CYCLE_COUNT = 4
DEFAULT_CYCLE_SHARES = (55.0, 25.0, 12.0, 8.0)
DEFAULT_FIRST_WEEK = 7


def default_cycles(count=DEFAULT_CYCLE_COUNT, first_week=DEFAULT_FIRST_WEEK):
	"""`count` cycles a week apart, front-loaded, adding to a hundred."""
	count = max(1, cint(count))
	if count == len(DEFAULT_CYCLE_SHARES):
		shares = list(DEFAULT_CYCLE_SHARES)
	else:
		# Halving weights, normalised: the first lift is always the big one.
		raw = [1.0 / (2 ** i) for i in range(count)]
		total = sum(raw)
		shares = [round(x * 100.0 / total, 2) for x in raw]
		shares[-1] = round(100.0 - sum(shares[:-1]), 2)
	return [{"lift_no": i + 1, "weeks_after": cint(first_week) + i,
	         "share_pct": shares[i]} for i in range(count)]


def _iso(d):
	if not d:
		return None
	y, w, _dow = getdate(d).isocalendar()
	return "%s-W%02d" % (y, w)


def _monday(d):
	d = getdate(d)
	return add_days(d, -d.weekday())


def stated_lifts(supplier=None, form=DEFAULT_FORM):
	"""The LIFTS a supplier hands one consignment over in, as Settings states.

	Not cycles. How often a house comes in a year is a commercial arrangement
	and nothing on file states it; how one consignment is then handed over, in
	lifts a week or two apart, is this. The two were read off the same table,
	so Iribov's two lifts were offered as "Cycles 2" and the card proposed a
	delivery rhythm the supplier had never agreed to."""
	rows = frappe.get_all(
		"Summer Flower Handover Wave",
		filters={"parent": "Summer Flower Settings", "form": form},
		fields=["source", "lift_no", "weeks_after_arrival", "share_pct"],
		order_by="lift_no asc")
	# A supplier's OWN profile, where one is on file. Where none is, four
	# cycles -- not the site's generic two, which is a different claim: that
	# every house works the same way.
	mine = [r for r in rows if supplier and r.source == supplier]
	if not mine and not supplier:
		mine = [r for r in rows if not r.source]
	if not mine:
		return []
	return [{"lift_no": cint(r.lift_no) or i + 1,
	         "weeks_after": cint(r.weeks_after_arrival),
	         "share_pct": flt(r.share_pct)}
	        for i, r in enumerate(mine)]


@frappe.whitelist()
def suppliers_for(form=DEFAULT_FORM):
	"""Every supplier that could sell this form, with the cycles it states."""
	named = {r.source for r in frappe.get_all(
		"Summer Flower Handover Wave",
		filters={"parent": "Summer Flower Settings", "form": form},
		fields=["source"]) if r.source}
	out = []
	for name in sorted(named):
		# The lift profile is what is on file for this house. It is offered for
		# the reader to see, never as a delivery rhythm: nobody has said how
		# many times a year this house comes, so nothing here says it either.
		out.append({"supplier": name, "lifts": stated_lifts(name, form),
		            "cycle_count": DEFAULT_CYCLE_COUNT, "stated": 1})
	return {"form": form, "default_cycles": default_cycles(),
	        "default_cycle_count": DEFAULT_CYCLE_COUNT,
	        "suppliers": out,
	        # Any supplier can be used; these are only the ones with a lift
	        # profile on file. Having a profile is not being chosen -- the card
	        # opens on nobody, because who supplies a season is a decision the
	        # farm makes and not one the settings table has already made.
	        "others": frappe.get_all("Supplier", filters={"disabled": 0},
	                                 pluck="name", order_by="name", limit=400)}


def supply_defaults(version):
	"""What the PROTOCOL says about buying this material, before any plan.

	Two figures the farm states once per variety and farm, rather than typing
	into every plan: how many times in a year a house delivering this form
	comes, and how many houses can supply it at all. They are not lifts -- a
	lift is how one consignment is handed over, a cycle is how often a
	consignment comes -- and the two were read off the same table for months,
	which is why a house with two lifts on file was planned as a house that
	comes twice a year.

	Both are advisory: they seed the card, and the farm overrules them on the
	plan. Zero means the protocol says nothing, which is not the same as zero
	suppliers."""
	from upande_summer_flowers.summer_flowers import route_ways, sourcing

	v = (version if hasattr(version, "material_route")
	     else frappe.get_cached_doc("Crop Protocol Version", version))
	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	form = route_ways.LIFT_FORM.get(entry)
	cycles = cint(v.get("supplier_cycles_per_year"))
	houses = cint(v.get("suppliers_available"))
	# A version that does not carry them falls back to the protocol as it
	# stands. These are commercial facts -- how often a house comes, how many
	# houses there are -- not agronomy, so changing one should not mean
	# re-approving a protocol, and every version approved before the figures
	# existed would otherwise be deaf to them forever.
	if (not cycles or not houses) and v.get("crop_protocol"):
		said = frappe.db.get_value(
			"Crop Protocol", v.crop_protocol,
			["custom_sf_supplier_cycles_per_year", "custom_sf_suppliers_available"],
			as_dict=True) or {}
		cycles = cycles or cint(said.get("custom_sf_supplier_cycles_per_year"))
		houses = houses or cint(said.get("custom_sf_suppliers_available"))
	cycles = max(0, min(52, cycles))
	return {
		"form": form or DEFAULT_FORM,
		"entry_stage": entry,
		# Nothing is bought on a route that starts in our own hands, so the
		# two figures are not asked for and not offered.
		"is_bought": bool(form),
		"cycles_a_year": cycles,
		"suppliers": max(0, min(12, houses)),
		"lifts": len(v.get("lifts") or []),
		"lead_weeks": cint(decided.get("lead_weeks"))
		              or cint(v.get("supplier_lead_weeks")),
		# How long a plant may wait if the house comes on the stated rhythm:
		# the gap between deliveries, less the week it is planted in.
		"wait_weeks": max(0, int(round(52.0 / cycles)) - 1) if cycles else 0,
	}


def _week_monday(week_iso):
	"""'2026-W12' -> the Monday of that week."""
	import re

	m = re.match(r"^(\d{4})-W(\d{1,2})$", str(week_iso or "").strip())
	if not m:
		return None
	try:
		return datetime.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
	except ValueError:
		return None


def _drops(weeks, wait):
	"""Which of these planting weeks a delivery opens in.

	A delivery opens, and carries the planting weeks behind it until one of
	them would have waited longer than `wait` weeks on the bench."""
	out = []
	for w in weeks:
		if not out or (getdate(w) - getdate(out[-1])).days > 7 * max(0, cint(wait)):
			out.append(w)
	return out


def proposal(plan_doc, version=None):
	"""What the protocol says this plan will have to be delivered.

	Worked out when the plan is drawn, before anyone has been named, from the
	three things the protocol already states: how often a house comes, how
	many houses there are, and how a consignment is handed over in lifts."""
	v = version or frappe.get_cached_doc("Crop Protocol Version",
	                                     plan_doc.protocol)
	d = supply_defaults(v)
	wanted = _wanted_by_week(plan_doc)
	weeks = sorted(wanted)
	drops = _drops(weeks, d["wait_weeks"]) if weeks else []
	d.update({"deliveries": len(drops), "weeks": [_iso(x) for x in drops],
	          "planting_weeks": len(weeks), "plants": sum(wanted.values())})
	return d


def proposal_note(plan_doc, version=None):
	"""The same thing in a sentence, for the plan to carry."""
	d = proposal(plan_doc, version)
	if not d["is_bought"]:
		return _("Nothing on this route is bought in, so there is no supply to "
		         "schedule.")
	if not d["deliveries"]:
		return _("Nothing to deliver to yet: this plan has no planting weeks.")
	bits = []
	if d["suppliers"]:
		bits.append(_("{0} supplier(s) available").format(d["suppliers"]))
	else:
		bits.append(_("no supplier count stated on the protocol"))
	if d["cycles_a_year"]:
		bits.append(_("{0} cycles a year, so a delivery every {1} weeks").format(
			d["cycles_a_year"], d["wait_weeks"] + 1))
	else:
		bits.append(_("no cycle count stated, so one delivery per planting week"))
	bits.append(_("{0} lift(s) per consignment").format(d["lifts"])
	            if d["lifts"] else _("no lift profile"))
	return _("{0}: {1}. On that footing this plan needs {2} deliveries "
	         "({3}) to cover {4} planting weeks. Nobody has been named yet.").format(
		d["form"], "; ".join(bits), d["deliveries"],
		", ".join(d["weeks"][:8]) + ("..." if len(d["weeks"]) > 8 else ""),
		d["planting_weeks"])


def _wanted_by_week(plan):
	"""{Monday of the planting week: plants that have to go in the ground}.

	From the plan's own plantings where it has any. Where it has none -- which
	is now the normal state of a draft, because nothing is planted until the
	supply is agreed -- from the demand itself. The houses are sized against
	what the market wants, not against a programme that does not exist yet."""
	out = {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		key = _monday(b.planting_date)
		out[key] = out.get(key, 0) + (cint(b.get("plants_to_plant")) or cint(b.plants))
	if out:
		return dict(sorted(out.items()))
	try:
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
			planting_requirement,
		)

		need, _from_demand = planting_requirement(plan)
		# What goes IN the ground. A buyer buys plants to plant, not plants
		# that will still be standing in six weeks.
		return {_monday(at): cint(to_plant) for at, _st, to_plant in need
		        if cint(to_plant)}
	except Exception:
		frappe.log_error(frappe.get_traceback(),
		                 "Demand sizing on %s" % plan.name)
		return {}


def _normalised(picks, form, wanted=None, default_count=None):
	"""The chosen houses: their share of the season, and how often they bring it.

	A cycle is a delivery: "four times a year" is four consignments, each
	carrying the plants wanted between it and the next one. The share of each
	is suggested from the demand in its window and can be overruled, which is
	how a house that insists on equal drops is planned for."""
	weeks = sorted(wanted or {})
	out = []
	for p in picks or []:
		name = (p.get("supplier") or "").strip()
		if not name:
			continue
		typed = [c for c in (p.get("cycles") or []) if c]
		# What the protocol states for this variety beats the module's guess of
		# four: the farm has said how often this house comes.
		count = (cint(p.get("cycle_count")) or len(typed)
		         or cint(default_count) or DEFAULT_CYCLE_COUNT)
		count = max(1, min(52, count))
		# Weeks stated outright beat any drumbeat. This is how a delivery is
		# added or taken away one at a time: the card hands back the weeks it
		# is showing, with one more or one fewer in the list.
		stated = []
		for wk in (p.get("weeks") or []):
			at = _monday(wk) if not str(wk).strip().count("-W") else _week_monday(wk)
			if at:
				stated.append(getdate(at))
		out.append({
			"supplier": name,
			"share_pct": flt(p.get("share_pct")),
			"lead_weeks": cint(p.get("lead_weeks")),
			"cycle_count": count,
			"weeks": sorted(set(stated)),
			"typed": [flt(c.get("share_pct")) for c in typed][:count],
			# A quantity typed against one delivery. The share was the only
			# way to say "this one brings more": a percentage of a total that
			# moves whenever any other row is touched, which is no way to
			# agree a consignment with a house. A number typed here is the
			# number ordered.
			"typed_units": [cint(c.get("units")) if c.get("units") not in
			                (None, "") else None for c in typed][:count],
		})
	# The share is ours to give: how much of the season each house is promised.
	# Typed shares are kept and only scaled to add to a hundred; where none are
	# typed the season is split evenly, which is a starting point, not a rule.
	share = sum(flt(x["share_pct"]) for x in out)
	if out and share <= 0:
		for x in out:
			x["share_pct"] = round(100.0 / len(out), 4)
	elif out and abs(share - 100.0) > 0.01:
		for x in out:
			x["share_pct"] = round(flt(x["share_pct"]) * 100.0 / share, 4)

	# When each delivery lands, and what the season wants between it and the
	# next. A year has 52 weeks whatever the plan covers, so "four a year" is a
	# delivery every thirteen weeks, counted from the first planting.
	if not weeks:
		return out
	first, last = weeks[0], weeks[-1]
	span_weeks = max(1, (getdate(last) - getdate(first)).days // 7 + 1)
	for x in out:
		# So many a YEAR, over however long the plan runs: a season of two
		# years at four a year is eight consignments, not four.
		step = max(1, int(round(52.0 / x["cycle_count"])))
		# A consignment lands on a week the farm is planting in. Counting the
		# drumbeat out from the first planting regardless of where the
		# plantings are put cycles on weeks with nothing to deliver to: a plan
		# planting in three tight pairs a year apart was given five cycles, two
		# of which carried nothing, covered nothing and were then dropped
		# again further down -- so the card asked for four, showed five and
		# ended on three. A cycle is started on the first planting week that is
		# at least a step on from the last one, so every cycle lands on a real
		# planting week and none of them is empty.
		if x.get("weeks"):
			# Stated outright. Kept as given, in order, so a week the farm
			# added by hand is not quietly moved onto the nearest planting.
			drops = list(x["weeks"])
		else:
			drops = []
			for w in weeks:
				if not drops or (getdate(w) - getdate(drops[-1])).days >= 7 * step:
					drops.append(w)
		x["drops_in_plan"] = len(drops)
		x["weeks_between"] = step
		# What was asked for, beside what the plan's own shape allows. Four a
		# year over a plan that only plants in three weeks is three cycles,
		# and saying so is better than quietly delivering a different number.
		x["cycles_a_year"] = x["cycle_count"]
		x["cycles_in_plan"] = len(drops)
		cycles = []
		for i, at in enumerate(drops):
			nxt = drops[i + 1] if i + 1 < len(drops) else None
			covers = [w for w in weeks
			          if getdate(w) >= getdate(at)
			          and (nxt is None or getdate(w) < getdate(nxt))]
			plants = sum(cint(wanted[w]) for w in covers)
			cycles.append({"lift_no": i + 1, "lands": at, "lands_iso": _iso(at),
			               "covers_from": _iso(at),
			               "covers_to": _iso(covers[-1]) if covers else _iso(at),
			               "planting_weeks": len(covers),
			               "suggested_plants": plants})
		total_plants = sum(c["suggested_plants"] for c in cycles) or 1
		for i, c in enumerate(cycles):
			typed = x["typed"][i] if i < len(x["typed"]) else None
			c["suggested_pct"] = round(c["suggested_plants"] * 100.0 / total_plants, 2)
			c["share_pct"] = flt(typed) if typed not in (None, "") else c["suggested_pct"]
			units = (x["typed_units"][i] if i < len(x.get("typed_units") or [])
			         else None)
			c["typed_units"] = cint(units) if units not in (None, "") else None
		given = sum(flt(c["share_pct"]) for c in cycles)
		if given <= 0:
			for c in cycles:
				c["share_pct"] = round(100.0 / len(cycles), 4)
		elif abs(given - 100.0) > 0.01:
			for c in cycles:
				c["share_pct"] = round(flt(c["share_pct"]) * 100.0 / given, 4)
		for c in cycles:
			c["share"] = flt(c["share_pct"]) / 100.0
		x["cycles"] = cycles
	return out


@frappe.whitelist()
def plan_supply(plan, picks=None, form=None):
	"""What to order each week from each supplier, once the carry is counted."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	if isinstance(picks, str):
		picks = frappe.parse_json(picks or "[]")
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)

	from upande_summer_flowers.summer_flowers import route_ways, sourcing

	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	form = form or route_ways.LIFT_FORM.get(entry) or DEFAULT_FORM

	wanted = _wanted_by_week(doc)
	stated = supply_defaults(v)
	chosen = _normalised(picks, form, wanted,
	                     default_count=stated["cycles_a_year"])
	# How often a house comes is one question; how each consignment is then
	# handed over, in lifts weeks apart, is another. The agreement carried only
	# the first, so every lifted crop was planned as though the whole order
	# became plants the week it landed.
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	for s_ in chosen:
		s_["handover"] = [{"lift_no": i + 1, "weeks_after": cint(wk),
		                   "share_pct": round(flt(sh) * 100, 2)}
		                  for i, (wk, sh) in enumerate(
			                  ts.handover_for(form, s_["supplier"], 0, v) or [])]
		first = cint(s_["handover"][0]["weeks_after"]) if s_["handover"] else 0
		s_["weeks_to_a_plant"] = first
		for c in (s_.get("cycles") or []):
			c["weeks_after"] = first
	if not chosen:
		return {"plan": plan, "form": form, "entry_stage": entry,
		        "suppliers": [], "weeks": [],
		        "reason": _("Choose at least one supplier to try.")}
	if not wanted:
		return {"plan": plan, "form": form, "entry_stage": entry,
		        "suppliers": chosen, "weeks": [],
		        "reason": _("This plan has no new plantings to supply.")}

	# One unit bought becomes this many plants in the ground, losses and all.
	# Taken from the same function the material card uses, so the two cards
	# cannot quote different orders for the same planting again.
	from upande_summer_flowers.summer_flowers import root_line as rl
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	per_unit = flt(rl.plants_per_unit_at_plant(v)) or (
		1.0 / (flt(v.cuttings_per_plant_required) or 1.0))
	need_total = sum(wanted.values())
	# A quantity typed on the material card is the quantity: the houses split
	# what was agreed to be bought, not what the demand asked for in a perfect
	# world. Where the two differ the weeks below say where it runs out.
	agreed_units = (cint(doc.get("tc_plants_committed"))
	                if cint(doc.get("tc_choice_committed")) else 0)
	buy_plants = (agreed_units * per_unit) if agreed_units else need_total

	# Every consignment this agreement puts on the ground: who brings it, when
	# it lands, what it carries and when it has to be ordered.
	landing, drops, orders = {}, [], []
	by_hand = {}
	for d in (doc.get("plan_deliveries") or []):
		at = _monday(d.week_start or _week_monday(d.week_iso))
		if not at:
			continue
		got = int(round(cint(d.units) * per_unit))
		by_hand[at] = cint(by_hand.get(at, 0)) + got
		landing[at] = cint(landing.get(at, 0)) + got
		drops.append({"supplier": d.supplier or _("by hand"), "by_hand": 1,
		              "cycle": None, "lands_iso": _iso(at), "lands": str(at),
		              "order_iso": None, "order_passed": 0,
		              "units": cint(d.units), "plants": got,
		              "covers_to": None, "planting_weeks": 0})

	# What a consignment of `plants` actually puts in the field, week by week.
	# A house that hands over in lifts does not deliver a planting, it delivers
	# the first lift of one; the rest follows a week or two later and belongs
	# to the weeks it lands in.
	def _spread(at, plants, waves):
		out, running = {}, 0
		first = cint(waves[0][0]) if waves else 0
		for i, (wk, share) in enumerate(waves or [(0, 1.0)]):
			got = (plants - running if i == len(waves) - 1
			       else int(round(plants * flt(share))))
			running += got
			if got <= 0:
				continue
			when = add_days(getdate(at), 7 * (cint(wk) - first))
			out[when] = cint(out.get(when, 0)) + got
		return out

	# Each house against its OWN share of each week, and its own cycles in the
	# order they land. A cycle is sized on what is still wanted of that share
	# -- not on the gross demand in its window -- because the tail of the
	# cycle before it has already landed in those weeks, and buying for them
	# twice is how a 10% lift came to be bought again as somebody else's 100%.
	#
	# Shares are kept because they are a decision: "Iribov brings 70% of the
	# season and ACME the rest" is how a farm arranges cover, and a sizing
	# that let whichever house landed first take the whole week would quietly
	# throw that arrangement away.
	# Each week's plants split between the houses so the parts add back to the
	# whole. Rounding each house's share on its own leaves a plant or two of
	# every week belonging to nobody, and a week one plant short is reported
	# as a week short -- which is how a plan with two houses on fifty each
	# could read "3 weeks short" against a week it covered exactly.
	split = {}
	for w, q in wanted.items():
		parts, given = [], 0
		for s_ in chosen:
			take = int(cint(q) * flt(s_["share_pct"]) / 100.0)
			parts.append([take, (cint(q) * flt(s_["share_pct"]) / 100.0) - take])
			given += take
		# The plants left over by flooring go to the largest remainders, which
		# is the one split that is both exact and stable.
		for i in sorted(range(len(parts)), key=lambda i: -parts[i][1])[:cint(q) - given]:
			parts[i][0] += 1
		split[w] = [p[0] for p in parts]

	for idx, s_ in enumerate(chosen):
		s_["waves"] = (ts.handover_for(form, s_["supplier"], 0, v) or [(0, 1.0)])
		mine = {w: split[w][idx] for w in wanted}
		covered = {}
		for c in sorted((s_.get("cycles") or []),
		                key=lambda x: getdate(_monday(x["lands"]))):
			at = getdate(_monday(c["lands"]))
			window = [w for w in mine
			          if getdate(w) >= at and (c.get("covers_to") is None
			                                   or _iso(w) <= c["covers_to"])]
			net = sum(max(0, cint(mine[w]) - cint(covered.get(w, 0)))
			          for w in window)
			# And big enough that the FIRST lift covers the week this
			# consignment is for. A house handing over 90/10 against a week
			# wanting 40,000 must be bought 44,445 for 40,000 to be there on
			# the day -- buying 40,000 puts 36,000 in the ground and leaves
			# the week 4,000 short with the balance arriving after it was
			# wanted.
			first_share = flt(s_["waves"][0][1]) if s_.get("waves") else 1.0
			first_need = (max(0, cint(mine[window[0]])
			                  - cint(covered.get(window[0], 0))) if window else 0)
			if first_need and 0 < first_share < 1:
				net = max(net, int(math.ceil(first_need / first_share - 1e-9)))
			c["net_plants"] = max(0, net)
			c["suggested_units"] = (int(math.ceil(c["net_plants"] / (per_unit or 1.0)
			                                      - 1e-9))
			                        if c["net_plants"] > 0 else 0)
			# Typed against this delivery: that is what is ordered, and the
			# weeks below are walked against what it really brings. A house
			# that will only send 50,000 is planned on 50,000, and the weeks
			# it leaves short say so instead of being quietly made up.
			if c.get("typed_units") is not None:
				c["net_plants"] = max(0, int(round(cint(c["typed_units"])
				                                   * (per_unit or 1.0))))
			for when, got in _spread(at, c["net_plants"], s_["waves"]).items():
				covered[when] = cint(covered.get(when, 0)) + got

	bought_total = 0
	for s_ in chosen:
		s_plants = buy_plants * flt(s_["share_pct"]) / 100.0
		for c in s_.get("cycles") or []:
			# Sized on what is still wanted where the demand is the guide. A
			# quantity typed on the material card still splits by share: that
			# is a decision, not an estimate.
			typed_units = c.get("typed_units")
			plants = (cint(c.get("net_plants"))
			          if (typed_units is not None or not agreed_units)
			          else int(round(s_plants * flt(c["share"]))))
			# Up, never to the nearest: half a plantlet short of a bed is a
			# bed that cannot be planted, and rounding down put the supply
			# card one unit under the material card on every order.
			units = (int(math.ceil(plants / (per_unit or 1.0) - 1e-9))
			         if plants > 0 else 0)
			# Typed is typed. Working the units back out of the plants that
			# quantity makes would hand the reader a number one off the one
			# they had just entered.
			if typed_units is not None:
				units = cint(typed_units)
			c["units"], c["plants"] = units, plants
			at = _monday(c["lands"])
			order_week = add_days(at, -7 * cint(s_["lead_weeks"]))
			c["order_iso"] = _iso(order_week)
			c["order_passed"] = 1 if getdate(order_week) < getdate(nowdate()) else 0
			if plants <= 0:
				continue
			# Landed in lifts, so the weekly walk below spends what has
			# really arrived. Counting the whole consignment at its first
			# lift said every week was covered that in fact was 10% short.
			for when, got in _spread(at, plants, s_["waves"]).items():
				landing[when] = cint(landing.get(when, 0)) + got
			bought_total += units
			c["order_iso"] = _iso(order_week)
			c["order_passed"] = 1 if getdate(order_week) < getdate(nowdate()) else 0
			drops.append({
				"supplier": s_["supplier"], "by_hand": 0, "cycle": c["lift_no"],
				"lands_iso": c["lands_iso"], "lands": str(at),
				"order_iso": c["order_iso"], "order_passed": c["order_passed"],
				"units": units, "plants": plants,
				"covers_to": c.get("covers_to"),
				"planting_weeks": cint(c.get("planting_weeks")),
			})
			orders.append({"supplier": s_["supplier"], "order_iso": c["order_iso"],
			               "units": units})
		s_["units"] = sum(cint(c.get("units")) for c in (s_.get("cycles") or []))
		s_["plants"] = sum(cint(c.get("plants")) for c in (s_.get("cycles") or []))
	drops.sort(key=lambda d: d["lands"])

	# Walk the planting weeks forward, spending what has landed. A consignment
	# carries the weeks between it and the next one, so plants wait on the
	# bench -- which is the cost of being delivered four times a year.
	rows = []
	stock = []
	short_total = 0
	held_max = 0
	for when, want in wanted.items():
		came = 0
		for at in sorted([k for k in landing if getdate(k) <= getdate(when)]):
			got = cint(landing.pop(at))
			came += got
			stock.append((at, got))
		in_hand = sum(q for _at, q in stock)
		take = min(in_hand, want)
		left, waited = take, 0
		while left > 0 and stock:
			at, q = stock[0]
			use = min(q, left)
			waited = max(waited, (getdate(when) - getdate(at)).days // 7)
			left -= use
			if use >= q:
				stock.pop(0)
			else:
				stock[0] = (at, q - use)
		held_max = max(held_max, waited)
		short = max(0, want - take)
		short_total += short
		rows.append({
			"week_iso": _iso(when), "week_start": str(when),
			"wanted": want,
			"by_hand": cint(by_hand.get(when, 0)),
			"landed": came,
			"planted": take,
			"held_weeks": waited,
			"short": short,
			"stock_left": sum(q for _at, q in stock),
			"drops": [d for d in drops if d["lands"] == str(when)],
		})

	# Anything that lands after the last planting week is bought and not used.
	stranded = sum(cint(x) for x in landing.values()) + sum(q for _at, q in stock)
	by_supplier = {}
	for o in orders:
		b = by_supplier.setdefault(o["supplier"], {"orders": 0, "first_order": None})
		b["orders"] += 1
		if not b["first_order"] or (o["order_iso"] or "") < (b["first_order"] or "~"):
			b["first_order"] = o["order_iso"]

	wanted_total = sum(wanted.values())
	out = {
		"plan": plan, "form": form, "entry_stage": entry,
		"suppliers": [dict(s, **by_supplier.get(s["supplier"], {})) for s in chosen],
		"weeks": rows,
		"drops": drops,
		"plants_wanted": wanted_total,
		"plants_short": short_total,
		"units_bought": bought_total,
		"plants_stranded": int(stranded),
		"weeks_short": sum(1 for r in rows if r["short"]),
		"weeks_total": len(rows),
		"cover_pct": (round((wanted_total - short_total) * 100.0 / wanted_total, 2)
		              if wanted_total else 0),
		"plants_per_unit": round(per_unit, 6),
		# What the material card agreed to buy, where it has agreed anything,
		# and what that stands up against what the plantings want.
		"units_agreed": agreed_units,
		"plants_from_agreed": int(round(buy_plants)) if agreed_units else None,
		"plants_needed": need_total,
		# The longest a later cycle's plants wait on the bench before a planting
		# week wants them. A profile that carries two weeks into a crop planted
		# ten weeks apart is not a carry, it is a write-off.
		"longest_held_weeks": held_max,
		"deliveries": [{"name": d.name, "week_iso": d.week_iso,
		                "supplier": d.supplier, "units": cint(d.units),
		                "plants": cint(d.plants), "note": d.note}
		               for d in (doc.get("plan_deliveries") or [])],
		"confirmed": cint(doc.get("supply_confirmed")),
		"docstatus": doc.docstatus,
	}
	# What it does to the plantings already on the plan, which is the question
	# behind choosing a supplier at all.
	out["calendar"] = _calendar_effect(doc, out)
	return out


def _calendar_effect(doc, result):
	"""What the chosen suppliers do to the plantings already on the plan."""
	by_week = {r["week_iso"]: r for r in result.get("weeks") or []}
	out = []
	for b in doc.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		key = _iso(_monday(b.planting_date))
		w = by_week.get(key) or {}
		want = cint(b.get("plants_to_plant")) or cint(b.plants)
		# A week is served pro rata when it is short: every planting in it
		# takes the same cut, because the material does not know whose it is.
		served = (flt(w.get("planted", 0)) / flt(w.get("wanted"))
		          if flt(w.get("wanted")) else 1.0)
		lands = int(round(want * served))
		out.append({
			"row": b.name,
			"block": b.block,
			"planting_iso": key,
			"planting_date": str(b.planting_date),
			"plants_planned": want,
			"plants_supplied": lands,
			"short": max(0, want - lands),
			"first_cut_iso": ("%d-W%02d" % (cint(b.first_harvest_year),
			                                cint(b.first_harvest_week))
			                  if cint(b.first_harvest_year) and cint(b.first_harvest_week)
			                  else None),
			"from_stock": cint(w.get("planted")) - cint(w.get("landed")),
			"waited_weeks": cint(w.get("held_weeks")),
			"ordered": [{"supplier": x["supplier"], "units": x["units"],
			             "order_iso": x["order_iso"], "passed": x["order_passed"]}
			            for x in (w.get("drops") or [])],
		})
	return out


@frappe.whitelist()
def save_supply(plan, picks, confirm=0):
	"""Write the chosen suppliers and cycles onto the plan, and agree them.

	The plan cannot be submitted until this is agreed, because what the houses
	will actually hand over, and when, is the plan -- not what the demand asked
	for in a perfect world."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is already submitted. Its supply was agreed when it "
		               "was.").format(doc.name))

	out = plan_supply(plan, picks=picks)
	if not out.get("weeks"):
		frappe.throw(out.get("reason") or _("There is nothing to supply."))

	units_by = {s["supplier"]: cint(s.get("units")) for s in out["suppliers"]}
	plants_by = {s["supplier"]: cint(s.get("plants")) for s in out["suppliers"]}

	doc.set("plan_supply", [])
	for s in out["suppliers"]:
		for i, c in enumerate(s["cycles"]):
			doc.append("plan_supply", {
				"supplier": s["supplier"],
				"share_pct": flt(s["share_pct"]),
				"lead_weeks": cint(s["lead_weeks"]),
				# How often they come, kept as the number that was chosen. The
				# rows below are what it came to over THIS plan, which is
				# fewer, and reading the count off them told a buyer their
				# four a year had become two.
				"cycle_count": cint(s.get("cycle_count")),
				"cycle_no": cint(c["lift_no"]) or i + 1,
				"weeks_after": cint(c.get("weeks_after")),
				"cycle_share_pct": flt(c["share_pct"]),
				# The week it lands and the quantity agreed on it. Without
				# these a saved supply reopened on a fresh rhythm at freshly
				# sized quantities, so a delivery moved by hand, or a figure a
				# house had actually agreed to, was lost on the next visit.
				"lands_iso": c.get("lands_iso"),
				"units_typed": cint(c.get("typed_units") or 0),
				# Stated on the first cycle only: it is the supplier's figure,
				# not the cycle's, and repeating it reads as four orders.
				"units": cint(units_by.get(s["supplier"], 0)) if not i else 0,
				"plants": cint(plants_by.get(s["supplier"], 0)) if not i else 0,
			})
	# Every consignment, not only the shape of the agreement: a buyer wants the
	# list of drops, and a list that only exists on a dashboard is one nobody
	# can hand to anybody.
	doc.set("plan_consignments", [])
	for d in out.get("drops") or []:
		doc.append("plan_consignments", {
			"supplier": d["supplier"], "cycle_no": cint(d.get("cycle")),
			"lands_week": d.get("lands_iso"), "lands_date": d.get("lands"),
			"units": cint(d.get("units")), "plants": cint(d.get("plants")),
			"order_week": d.get("order_iso"), "covers_to": d.get("covers_to"),
			"planting_weeks": cint(d.get("planting_weeks")),
			"by_hand": cint(d.get("by_hand")),
		})
	doc.supply_units = cint(out["units_bought"])
	doc.supply_short_plants = cint(out["plants_short"])
	doc.supply_stranded_plants = cint(out["plants_stranded"])
	short = cint(out["plants_short"])
	doc.supply_note = _(
		"{0} supplying {1} plants in {2}. {3} to buy; {4} of {5} planting "
		"weeks covered{6}."
	).format(
		" and ".join(
			"{} ({}%, {} cycles a year)".format(
				s["supplier"], round(flt(s["share_pct"])), cint(s["cycle_count"]))
			for s in out["suppliers"]),
		"{:,}".format(cint(out["plants_wanted"])),
		_("{0} cycles over the plan").format(
			sum(cint(s.get("drops_in_plan")) for s in out["suppliers"])),
		"{:,} {}".format(cint(out["units_bought"]), out["form"]),
		out["weeks_total"] - out["weeks_short"], out["weeks_total"],
		(_(", {0} plant short") if short == 1 else _(", {0} plants short")
		 ).format("{:,}".format(short)) if short else "")
	# One place names the sellers. Choosing cycles for one house on this card
	# and buying from another on the procurement card is two decisions where
	# the farm only ever makes one, so what is agreed here is written through.
	_write_sellers(doc, out)

	if cint(confirm):
		doc.supply_confirmed = 1
		doc.supply_confirmed_on = frappe.utils.now()
		doc.supply_confirmed_by = frappe.session.user

	# And now the plan itself. Until this moment it had no new plantings at
	# all: a draft says what the market wants and what is already standing,
	# and stops there. The programme is drawn from the consignments just
	# written -- every lift of every one of them, each in the week it becomes
	# a plant the field can take -- so the plantings, the weekly grid, the
	# space and the material line are all answers to what was agreed.
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		rebuild_from_supply,
	)

	rebuild_from_supply(doc)
	out["settled"] = _settle_from_agreement(doc, out)
	doc.flags.ignore_validate_update_after_submit = True
	doc.save()
	out["plantings"] = len([b for b in doc.plan_blocks
	                        if cint(b.is_new_planting)])
	out["plants_planted"] = sum(cint(b.plants) for b in doc.plan_blocks
	                            if cint(b.is_new_planting))
	out["saved"] = 1
	out["confirmed"] = cint(doc.supply_confirmed)
	out["calendar"] = _calendar_effect(doc, out)
	out["note"] = doc.supply_note
	return out


def _settle_from_agreement(doc, out):
	"""Re-date the draft from what the houses actually agreed to.

	A draft is dated off the protocol -- what the CROP does, which is the only
	thing known before anybody has been asked. Once the houses and their cycles
	are agreed, what THEY do is the plan: their lead times and their lifts.

	Only the dates the agreement decides are moved. Which weeks are planted,
	and how much goes in each, is the demand's answer and no supplier's
	business -- a house that is slow moves the order earlier, it does not get
	to move the harvest."""
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		_cut_from_a_pool,
	)

	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	# A crop cut from our own standing pool is stuck off that pool. What a
	# house hands over feeds the pool; it does not date the sticking.
	if _cut_from_a_pool(v):
		return None
	# Where two houses hand over on different rhythms the binding one is the
	# slowest: the week is only supplied once the last of them has landed.
	weeks = max([cint(s.get("weeks_to_a_plant"))
	             for s in (out.get("suppliers") or [])] or [0])
	if not weeks:
		return None
	moved, rows = 0, 0
	for b in doc.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		rows += 1
		y, w, _dow = add_days(getdate(b.planting_date), -7 * weeks).isocalendar()
		if cint(b.sticking_year) != cint(y) or cint(b.sticking_week) != cint(w):
			moved += 1
		b.sticking_year, b.sticking_week = cint(y), cint(w)
	orders = sorted(d.get("order_iso") for d in (out.get("drops") or [])
	                if d.get("order_iso"))
	return {"weeks_to_a_plant": weeks, "rows": rows, "rows_moved": moved,
	        "order_week": orders[0] if orders else None,
	        "by_supplier": [{"supplier": s["supplier"],
	                         "lead_weeks": cint(s.get("lead_weeks")),
	                         "weeks_to_a_plant": cint(s.get("weeks_to_a_plant")),
	                         "lifts": len(s.get("handover") or [])}
	                        for s in (out.get("suppliers") or [])]}


def _write_sellers(plan_doc, out):
	"""Put the agreed houses on the draft procurement plan as its sellers."""
	name = frappe.db.get_value(
		"Summer Flower Procurement Plan",
		{"production_plan": plan_doc.name, "docstatus": 0}, "name")
	if not name:
		return None
	proc = frappe.get_doc("Summer Flower Procurement Plan", name)
	stage = proc.entry_stage or out.get("entry_stage")
	proc.set("purchase_allocation", [])
	for s in out.get("suppliers") or []:
		proc.append("purchase_allocation", {
			"entry_stage": stage,
			"supplier": s["supplier"],
			"share_pct": flt(s["share_pct"]),
			"lead_weeks": cint(s.get("lead_weeks")),
		})
	proc.flags.choosing_sellers = True
	proc.flags.ignore_permissions = True
	proc.save()
	return proc.name


@frappe.whitelist()
def clear_supply(plan):
	"""Un-agree the supply, so it can be chosen again."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is submitted.").format(doc.name))
	doc.supply_confirmed = 0
	doc.supply_confirmed_on = None
	doc.supply_confirmed_by = None
	# Un-agreeing takes the programme away with it: the plantings were the
	# consignments' own, and without them there is nothing holding them up.
	doc.set("plan_consignments", [])
	doc.set("plan_supply", [])
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		_populate,
	)

	_populate(doc)
	doc.save()
	return {"plan": doc.name, "confirmed": 0,
	        "plantings": len([b for b in doc.plan_blocks
	                          if cint(b.is_new_planting)])}


@frappe.whitelist()
def saved_supply(plan):
	"""What the plan has agreed to, for the card to open on."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	picks = {}
	for r in (doc.get("plan_supply") or []):
		p = picks.setdefault(r.supplier, {"supplier": r.supplier,
		                                  "share_pct": flt(r.share_pct),
		                                  "lead_weeks": cint(r.lead_weeks),
		                                  "cycle_count": cint(r.get("cycle_count")),
		                                  "cycles": []})
		if r.get("lands_iso"):
			p.setdefault("weeks", []).append(r.lands_iso)
		p["cycles"].append({"lift_no": cint(r.cycle_no),
		                    "weeks_after": cint(r.weeks_after),
		                    "share_pct": flt(r.cycle_share_pct),
		                    "units": cint(r.get("units_typed")) or None})
	return {"plan": doc.name, "picks": list(picks.values()),
	        # What the protocol states about buying this material, so the card
	        # opens on the farm's own agreement rather than on a module guess.
	        "defaults": supply_defaults(doc.protocol) if doc.protocol else None,
	        "deliveries": [{"name": d.name, "week_iso": d.week_iso,
	                        "supplier": d.supplier, "units": cint(d.units),
	                        "plants": cint(d.plants), "note": d.note}
	                       for d in (doc.get("plan_deliveries") or [])],
	        "confirmed": cint(doc.supply_confirmed),
	        "confirmed_on": str(doc.supply_confirmed_on or "") or None,
	        "confirmed_by": doc.supply_confirmed_by,
	        "note": doc.supply_note,
	        "docstatus": doc.docstatus}


@frappe.whitelist()
def save_delivery(plan, week_iso, units, supplier=None, note=None, row=None):
	"""Add a delivery against a week, or change one already typed in."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is submitted.").format(doc.name))
	monday = _week_monday(week_iso)
	if not monday:
		frappe.throw(_("{0} is not a week. Weeks look like 2026-W12.").format(week_iso))
	if cint(units) <= 0:
		frappe.throw(_("A delivery of nothing is not a delivery."))

	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	per_unit = 1.0 / (flt(v.cuttings_per_plant_required) or 1.0)
	existing = next((d for d in (doc.get("plan_deliveries") or [])
	                 if d.name == row), None) if row else None
	target = existing or doc.append("plan_deliveries", {})
	target.week_iso = _iso(monday)
	target.week_start = monday
	target.supplier = supplier or None
	target.units = cint(units)
	target.plants = int(round(cint(units) * per_unit))
	target.note = note
	# Typing a delivery changes what was agreed, so the agreement is re-asked.
	doc.supply_confirmed = 0
	doc.supply_confirmed_on = None
	doc.supply_confirmed_by = None
	doc.save()
	return saved_supply(doc.name)


@frappe.whitelist()
def delete_delivery(plan, row):
	"""Take a typed-in delivery back off the plan."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is submitted.").format(doc.name))
	keep = [d for d in (doc.get("plan_deliveries") or []) if d.name != row]
	if len(keep) == len(doc.get("plan_deliveries") or []):
		frappe.throw(_("That delivery is not on this plan."))
	doc.set("plan_deliveries", [])
	for d in keep:
		doc.append("plan_deliveries", {
			"week_iso": d.week_iso, "week_start": d.week_start,
			"supplier": d.supplier, "units": d.units, "plants": d.plants,
			"note": d.note})
	doc.supply_confirmed = 0
	doc.supply_confirmed_on = None
	doc.supply_confirmed_by = None
	doc.save()
	return saved_supply(doc.name)


@frappe.whitelist()
def pull_procurement(plan):
	"""Copy what the sellers and raisers agreed to onto the plan itself.

	The plan is the document people keep. Leaving the agreement only on the
	procurement plan meant reading two records to know what a season was
	actually built on, and the second one outlives nobody's memory of it."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)

	proc = frappe.get_all(
		"Summer Flower Procurement Plan",
		filters={"production_plan": doc.name, "docstatus": ["<", 2]},
		fields=["name", "status", "docstatus", "entry_stage"],
		order_by="creation desc", limit=1)
	proc = proc[0] if proc else None

	# What was ordered is the Supply tab's business; what comes back here is
	# who raises the plants and when they hand them over.
	raisers = []
	plants = outside_cost = 0
	units = outsiders = 0
	if proc:
		p = frappe.get_doc("Summer Flower Procurement Plan", proc.name)
		for a in (p.get("propagation_allocation") or []):
			out = a.alloc_type == "Outside Propagator"
			plants += cint(a.plants)
			units += 0 if out else 1
			outsiders += 1 if out else 0
			outside_cost += flt(a.amount) if out else 0
			raisers.append({
				"alloc_type": a.alloc_type, "party": a.party or a.supplier
				or a.propagation_unit, "raises": a.raises,
			"lead_weeks": cint(a.lead_weeks),
				"share_pct": flt(a.share_pct), "plants": cint(a.plants),
				"rate_per_plant": flt(a.rate_per_plant), "amount": flt(a.amount),
				"start_by": a.start_by, "plants_from": a.plants_from,
				"plants_to": a.plants_to,
				"propagation_plan": a.propagation_plan,
				"purchase_order": a.purchase_order,
			})

	if doc.docstatus == 2:
		frappe.throw(_("{0} is cancelled. Its revision carries the agreement."
		               ).format(doc.name))

	scalars = {}
	if raisers:
		scalars.update({
			"propagation_plants": plants,
			"propagation_cost": outside_cost,
			"propagation_units": units,
			"propagation_outside": outsiders,
		})
	# Only where a procurement plan actually says who raises it. A plan that
	# has raised nothing yet still has the choice made on the planning board,
	# and overwriting it with an empty list loses the only copy.
	tables = ({"plan_raisers": ("Summer Flower Plan Raiser", raisers)}
	          if raisers else {})
	if doc.docstatus == 0:
		for field, (_dt, rows) in tables.items():
			doc.set(field, [])
			for r in rows:
				doc.append(field, r)
		for k, v in scalars.items():
			setattr(doc, k, v)
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
	else:
		# A submitted plan still has to be able to say what came back: these
		# are a record of what happened, not an edit to what was approved.
		for field, (child_dt, rows) in tables.items():
			frappe.db.delete(child_dt, {"parent": doc.name, "parentfield": field})
			for i, r in enumerate(rows):
				child = frappe.get_doc(dict(
					r, doctype=child_dt, parent=doc.name, parenttype=doc.doctype,
					parentfield=field, idx=i + 1))
				child.db_insert()
		for k, v in scalars.items():
			doc.db_set(k, v, update_modified=False)
	doc.reload()
	arrivals = _write_arrivals(doc)
	if doc.docstatus == 0 and arrivals:
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
	return {"plan": doc.name, "procurement_plan": proc.name if proc else None,
	        "raisers": len(raisers), "arrivals": len(arrivals),
	        "plants": plants, "outside_cost": outside_cost}


def _lead_for_party(unit, supplier, plan_doc):
	"""Weeks of notice: the supplier's own, else the protocol's own bench."""
	if supplier:
		return cint(frappe.db.get_value("Supplier", supplier,
		                                "custom_sf_propagation_weeks"))
	try:
		v = frappe.get_cached_doc("Crop Protocol Version", plan_doc.protocol)
		return cint(v.get("weeks_tc_to_first_cut_derived")) or cint(
			v.get("sticking_to_planting_weeks"))
	except Exception:
		frappe.clear_last_message()
		return 0


@frappe.whitelist()
def save_raisers(plan, rows):
	"""Who raises the plants, kept on the plan itself.

	Until now this lived only on the procurement plan, which does not exist
	until the plan has been approved -- so a draft had nowhere to record the
	choice and the Propagation tab read zero on a plan whose units had been
	picked weeks before."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	rows = frappe.parse_json(rows) if isinstance(rows, str) else (rows or [])
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus == 2:
		frappe.throw(_("{0} is cancelled.").format(doc.name))

	plants_total = cint(doc.get("new_plants_required"))
	kept, plants, cost, units, outsiders = [], 0, 0.0, 0, 0
	for r in rows:
		party = (r.get("party") or r.get("propagation_unit") or r.get("supplier") or "")
		if not party:
			continue
		outside = bool(r.get("supplier")) and not r.get("propagation_unit")
		share = flt(r.get("share_pct"))
		mine = cint(r.get("plants")) or int(round(plants_total * share / 100.0))
		rate = flt(r.get("rate_per_plant"))
		plants += mine
		cost += rate * mine if outside else 0
		units += 0 if outside else 1
		outsiders += 1 if outside else 0
		kept.append({
			"alloc_type": _("Outside Propagator") if outside else _("Own Unit"),
			"party": party, "raises": r.get("raises") or doc.get("entry_stage"),
			"share_pct": share, "plants": mine,
			"rate_per_plant": rate, "amount": rate * mine if outside else 0,
			"start_by": r.get("start_by"), "plants_from": r.get("plants_from"),
			"plants_to": r.get("plants_to"),
			# Kept so the arrivals can say when this raiser has to start, not
			# only when the field wants what they bring.
			"lead_weeks": cint(r.get("lead_weeks")) or _lead_for_party(
				r.get("propagation_unit"), r.get("supplier"), doc),
		})

	if doc.docstatus == 0:
		doc.set("plan_raisers", [])
		for r in kept:
			doc.append("plan_raisers", r)
		_write_arrivals(doc)
		doc.propagation_plants = plants
		doc.propagation_cost = cost
		doc.propagation_units = units
		doc.propagation_outside = outsiders
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
	else:
		frappe.db.delete("Summer Flower Plan Raiser",
		                 {"parent": doc.name, "parentfield": "plan_raisers"})
		for i, r in enumerate(kept):
			frappe.get_doc(dict(r, doctype="Summer Flower Plan Raiser",
			                    parent=doc.name, parenttype=doc.doctype,
			                    parentfield="plan_raisers", idx=i + 1)).db_insert()
		doc.reload()
		_write_arrivals(doc)
		for k, v in (("propagation_plants", plants), ("propagation_cost", cost),
		             ("propagation_units", units), ("propagation_outside", outsiders)):
			doc.db_set(k, v, update_modified=False)
	return {"plan": doc.name, "raisers": len(kept), "plants": plants,
	        "cost": cost, "units": units, "outside": outsiders}


def _arrival_rows(plan_doc):
	"""Week by week, what each raiser hands to the field."""
	from upande_summer_flowers.summer_flowers import planning_api as pa

	name = frappe.db.get_value(
		"Summer Flower Procurement Plan",
		{"production_plan": plan_doc.name, "docstatus": ["<", 2]}, "name")
	out = []
	if name:
		# The procurement plan knows the lead time each raiser works to, so
		# its own schedule is used rather than a second one worked out here.
		for r in (pa.allocation_plan(name) or {}).get("raisers") or []:
			for w in r.get("weeks") or []:
				out.append({
					"week_iso": w["wanted_iso"], "week_start": w["wanted_date"],
					"party": r["party"],
					"alloc_type": (_("Outside Propagator") if r["outside"]
					               else _("Own Unit")),
					"plants_handed": cint(w["plants_handed"]),
					"plants_standing": cint(w["plants_standing"]),
					"units": cint(w["units"]), "start_week": w["start_iso"],
				})
	if out:
		return sorted(out, key=lambda x: (x["week_start"], x["party"]))

	# Nothing raised yet: split what the plantings want by the shares chosen
	# on the plan, so a draft still says who brings what and when.
	rows = [r for r in (plan_doc.get("plan_raisers") or []) if flt(r.share_pct)]
	if not rows:
		return []
	for b in plan_doc.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		at = _monday(b.planting_date)
		hand = cint(b.get("plants_to_plant")) or cint(b.plants)
		for r in rows:
			share = flt(r.share_pct) / 100.0
			out.append({
				"week_iso": _iso(at), "week_start": str(at), "party": r.party,
				"alloc_type": r.alloc_type,
				"plants_handed": int(round(hand * share)),
				"plants_standing": int(round(cint(b.plants) * share)),
				"units": 0,
				"start_week": _iso(add_days(at, -7 * cint(r.get("lead_weeks")))),
			})
	merged = {}
	for x in out:
		k = (x["week_start"], x["party"])
		if k in merged:
			merged[k]["plants_handed"] += x["plants_handed"]
			merged[k]["plants_standing"] += x["plants_standing"]
		else:
			merged[k] = x
	return [merged[k] for k in sorted(merged)]


def _write_arrivals(plan_doc):
	"""Keep the weekly arrivals on the plan, draft or submitted."""
	rows = _arrival_rows(plan_doc)
	if plan_doc.docstatus == 0:
		plan_doc.set("plan_arrivals", [])
		for r in rows:
			plan_doc.append("plan_arrivals", r)
		return rows
	frappe.db.delete("Summer Flower Plan Arrival",
	                 {"parent": plan_doc.name, "parentfield": "plan_arrivals"})
	for i, r in enumerate(rows):
		frappe.get_doc(dict(r, doctype="Summer Flower Plan Arrival",
		                    parent=plan_doc.name, parenttype=plan_doc.doctype,
		                    parentfield="plan_arrivals", idx=i + 1)).db_insert()
	return rows


@frappe.whitelist()
def consignments_csv(plan):
	"""The deliveries, as a file somebody can send to a supplier."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	rows = [["Supplier", "Delivery", "Lands (week)", "Lands (date)", "Units",
	         "Plants", "Order by", "Covers plantings to", "Planting weeks",
	         "Added by hand"]]
	for c in (doc.get("plan_consignments") or []):
		rows.append([c.supplier, cint(c.cycle_no), c.lands_week,
		             str(c.lands_date or ""), cint(c.units), cint(c.plants),
		             c.order_week, c.covers_to, cint(c.planting_weeks),
		             "yes" if cint(c.by_hand) else ""])
	import csv
	import io

	buf = io.StringIO()
	csv.writer(buf).writerows(rows)
	frappe.response["filename"] = "%s-deliveries.csv" % doc.name
	frappe.response["filecontent"] = buf.getvalue()
	frappe.response["type"] = "download"
	frappe.response["doctype"] = None


@frappe.whitelist()
def suggest_supply(plan, houses=None, max_wait_weeks=None):
	"""What the plan would have to be delivered, before anyone is named.

	The farm's question is not "here are my houses, what do they bring" but
	"what has to arrive, when, and how many houses do I need to bring it".
	This answers that from the two things already known -- the weeks the plan
	plants in, and how a consignment is handed over in lifts -- and leaves the
	names blank, because who supplies a season is the farm's decision and not
	an arithmetic result.

	`max_wait_weeks` is how long plants may sit on the bench waiting for their
	planting week. Nought is a delivery for every planting week: no waiting, no
	surplus, the most deliveries. Raising it folds the later weeks into the
	consignment before them, which is fewer deliveries and plants waiting.

	Neither number has to be typed. Left out, both are read off the protocol:
	how many houses can supply this variety, and how often a house comes --
	from which the waiting follows, because a house that comes four times a
	year is a delivery every thirteen weeks whether or not anyone says so.
	"""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	stated = supply_defaults(v)

	# What the farm typed on the card wins; what it did not type comes off the
	# protocol; and only where the protocol is silent does the old default
	# stand -- one house, and a delivery for every planting week.
	def _given(x):
		# Nought is an answer -- "a delivery for every planting week" -- and
		# `x or ""` would read it as silence, so the test is for the absence
		# of a value, not for its falseness.
		return x is not None and str(x).strip() not in ("", "None")

	asked_houses = cint(houses) if _given(houses) else 0
	houses = max(1, min(12, asked_houses or stated["suppliers"] or 1))
	wait = max(0, cint(max_wait_weeks)) if _given(max_wait_weeks) \
		else stated["wait_weeks"]

	from upande_summer_flowers.summer_flowers import route_ways, sourcing

	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	form = route_ways.LIFT_FORM.get(entry) or DEFAULT_FORM
	# The route states the notice the entry stage needs; the lab turnaround is
	# the fallback, and ten weeks the last resort.
	lead = cint(decided.get("lead_weeks")) or cint(
		frappe.db.get_single_value("Summer Flower Settings",
		                           "lab_turnaround_weeks")) or 10

	wanted = _wanted_by_week(doc)
	weeks = sorted(wanted)
	if not weeks:
		return {"plan": plan, "form": form, "houses": [], "picks": [],
		        "reason": _("This plan has no planting weeks to deliver to.")}

	# A delivery opens, and carries the planting weeks behind it until one of
	# them would have waited longer than the farm is willing to carry it.
	drops = _drops(weeks, wait)

	span_weeks = max(1, (getdate(weeks[-1]) - getdate(weeks[0])).days // 7 + 1)
	years = max(span_weeks / 52.0, 1.0 / 52.0)
	# Every house delivers against every one of those weeks, each bringing its
	# own share of it. Dealing the weeks out one house at a time instead --
	# this one takes week 28, that one week 29 -- reads neatly and plans
	# wrongly: a house given a quarter of the season still has to cover the
	# WHOLE of the weeks it was dealt, and sizing it on a quarter leaves every
	# one of those weeks short. Sharing each delivery is also how the farm
	# actually arranges cover, so that a week nobody can fill alone is filled
	# between them.
	share = round(100.0 / houses, 4)
	picks = [{
		"supplier": "",
		"share_pct": share if i < houses - 1 else round(100.0 - share * (houses - 1), 4),
		"lead_weeks": lead,
		# How often this house comes. The protocol's figure is the agreement;
		# what the plan worked out is only what these planting weeks needed,
		# and a house does not come less often because one season is short.
		"cycle_count": max(1, stated["cycles_a_year"]
		                   or int(round(len(drops) / years))),
		"weeks": [_iso(d) for d in drops],
	} for i in range(houses)]

	# What those deliveries actually come to, worked out by the same function
	# the card uses, so a suggestion and the plan it becomes cannot differ.
	named = [dict(p, supplier=_("House {0}").format(i + 1))
	         for i, p in enumerate(picks)]
	run = plan_supply(plan, picks=named, form=form)
	return {
		"plan": plan, "form": form, "entry_stage": entry,
		"lead_weeks": lead,
		# Where each number came from, so the card can say so rather than
		# presenting the protocol's agreement as arithmetic of its own.
		"from_protocol": stated,
		"houses_asked": asked_houses,
		"houses_suggested": len(picks),
		"deliveries": len(drops),
		"planting_weeks": len(weeks),
		"max_wait_weeks": wait,
		"cycles_a_year": [p["cycle_count"] for p in picks],
		# Ready to drop straight into the card: shares, cycles and the weeks
		# themselves, with the names left for the farm to fill in.
		"picks": picks,
		"would_buy": cint(run.get("units_bought")),
		"weeks_short": cint(run.get("weeks_short")),
		"longest_wait_weeks": max(
			[cint(w.get("held_weeks")) for w in (run.get("weeks") or [])] or [0]),
		"stranded": cint(run.get("plants_stranded")),
		"plants_needed": cint(run.get("plants_needed")),
		"preview": run,
	}
