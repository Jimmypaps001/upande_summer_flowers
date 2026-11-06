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


def stated_cycles(supplier=None, form=DEFAULT_FORM):
	"""The cycles a supplier hands a consignment over in, as Settings states."""
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
		return default_cycles()
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
		out.append({"supplier": name, "cycles": stated_cycles(name, form),
		            "stated": 1})
	return {"form": form, "default_cycles": stated_cycles(None, form) or default_cycles(),
	        "default_cycle_count": DEFAULT_CYCLE_COUNT,
	        "suppliers": out,
	        # Any supplier can be used; these are only the ones with a profile
	        # on file. The rest start from the default and are edited on screen.
	        "others": frappe.get_all("Supplier", filters={"disabled": 0},
	                                 pluck="name", order_by="name", limit=400)}


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


def _wanted_by_week(plan):
	"""{Monday of the planting week: plants that have to go in the ground}."""
	out = {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		key = _monday(b.planting_date)
		out[key] = out.get(key, 0) + (cint(b.get("plants_to_plant")) or cint(b.plants))
	return dict(sorted(out.items()))


def _normalised(picks, form, wanted=None):
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
		count = cint(p.get("cycle_count")) or len(typed) or DEFAULT_CYCLE_COUNT
		count = max(1, min(52, count))
		out.append({
			"supplier": name,
			"share_pct": flt(p.get("share_pct")),
			"lead_weeks": cint(p.get("lead_weeks")),
			"cycle_count": count,
			"typed": [flt(c.get("share_pct")) for c in typed][:count],
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
		drops, k = [], 0
		while len(drops) < 52:
			at = add_days(first, 7 * step * k)
			if k and getdate(at) > getdate(last):
				break
			drops.append(at)
			k += 1
		x["drops_in_plan"] = len(drops)
		x["weeks_between"] = step
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
	chosen = _normalised(picks, form, wanted)
	if not chosen:
		return {"plan": plan, "form": form, "entry_stage": entry,
		        "suppliers": [], "weeks": [],
		        "reason": _("Choose at least one supplier to try.")}
	if not wanted:
		return {"plan": plan, "form": form, "entry_stage": entry,
		        "suppliers": chosen, "weeks": [],
		        "reason": _("This plan has no new plantings to supply.")}

	# One unit bought becomes this many plants in the ground, losses and all.
	per_unit = 1.0 / (flt(v.cuttings_per_plant_required) or 1.0)
	need_total = sum(wanted.values())

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

	bought_total = 0
	for s_ in chosen:
		s_plants = need_total * flt(s_["share_pct"]) / 100.0
		for c in s_.get("cycles") or []:
			plants = int(round(s_plants * flt(c["share"])))
			units = int(round(plants / (per_unit or 1.0))) if plants > 0 else 0
			c["units"], c["plants"] = units, plants
			at = _monday(c["lands"])
			order_week = add_days(at, -7 * cint(s_["lead_weeks"]))
			c["order_iso"] = _iso(order_week)
			c["order_passed"] = 1 if getdate(order_week) < getdate(nowdate()) else 0
			if plants <= 0:
				continue
			landing[at] = cint(landing.get(at, 0)) + plants
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
				"cycle_no": cint(c["lift_no"]) or i + 1,
				"weeks_after": cint(c.get("weeks_after")),
				"cycle_share_pct": flt(c["share_pct"]),
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
			"{} ({}% over {} deliveries a year)".format(
				s["supplier"], round(flt(s["share_pct"])), cint(s["cycle_count"]))
			for s in out["suppliers"]),
		"{:,}".format(cint(out["plants_wanted"])),
		_("{0} consignments").format(
			sum(cint(s.get("drops_in_plan")) for s in out["suppliers"])),
		"{:,} {}".format(cint(out["units_bought"]), out["form"]),
		out["weeks_total"] - out["weeks_short"], out["weeks_total"],
		(_(", {0} plant short") if short == 1 else _(", {0} plants short")
		 ).format("{:,}".format(short)) if short else "")
	if cint(confirm):
		doc.supply_confirmed = 1
		doc.supply_confirmed_on = frappe.utils.now()
		doc.supply_confirmed_by = frappe.session.user
	doc.flags.ignore_validate_update_after_submit = True
	doc.save()
	out["saved"] = 1
	out["confirmed"] = cint(doc.supply_confirmed)
	out["calendar"] = _calendar_effect(doc, out)
	out["note"] = doc.supply_note
	return out


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
	doc.save()
	return {"plan": doc.name, "confirmed": 0}


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
		                                  "cycles": []})
		p["cycles"].append({"lift_no": cint(r.cycle_no),
		                    "weeks_after": cint(r.weeks_after),
		                    "share_pct": flt(r.cycle_share_pct)})
	return {"plan": doc.name, "picks": list(picks.values()),
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

	orders, raisers = [], []
	asked = confirmed = 0
	plants = outside_cost = 0
	units = outsiders = 0
	if proc:
		p = frappe.get_doc("Summer Flower Procurement Plan", proc.name)
		for r in p.requirements:
			want = cint(r.qty_to_order)
			got = (cint(r.confirmed_qty) if cint(r.get("delivery_confirmed"))
			       else want)
			asked += want
			confirmed += got
			orders.append({
				"week_iso": _iso(r.required_at_site_date),
				"supplier": r.supplier or p.supplier,
				"stage": r.entry_stage or p.entry_stage,
				"asked_units": want, "confirmed_units": got,
				"delivery_week": (r.confirmed_delivery_week
				                  or _iso(r.expected_delivery_date)),
				"state": (_("short {0}").format("{:,}".format(want - got))
				          if got < want else _("as asked")),
			})
		for a in (p.get("propagation_allocation") or []):
			out = a.alloc_type == "Outside Propagator"
			plants += cint(a.plants)
			units += 0 if out else 1
			outsiders += 1 if out else 0
			outside_cost += flt(a.amount) if out else 0
			raisers.append({
				"alloc_type": a.alloc_type, "party": a.party or a.supplier
				or a.propagation_unit, "raises": a.raises,
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

	scalars = {
		"procurement_plan": proc.name if proc else None,
		"procurement_status": (proc.status if proc else _("nothing raised yet")),
		"procurement_units_asked": asked,
		"procurement_units_confirmed": confirmed,
		"procurement_short_units": max(0, asked - confirmed),
	}
	if raisers:
		scalars.update({
			"propagation_plants": plants,
			"propagation_cost": outside_cost,
			"propagation_units": units,
			"propagation_outside": outsiders,
		})
	tables = {"plan_orders": ("Summer Flower Plan Order", orders)}
	# Only where a procurement plan actually says who raises it. A plan that
	# has raised nothing yet still has the choice made on the planning board,
	# and overwriting it with an empty list loses the only copy.
	if raisers:
		tables["plan_raisers"] = ("Summer Flower Plan Raiser", raisers)
	else:
		pass
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
	return {"plan": doc.name, "procurement_plan": doc.procurement_plan,
	        "orders": len(orders), "raisers": len(raisers),
	        "units_asked": asked, "units_confirmed": confirmed,
	        "short": doc.procurement_short_units,
	        "plants": plants, "outside_cost": outside_cost}


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
		})

	if doc.docstatus == 0:
		doc.set("plan_raisers", [])
		for r in kept:
			doc.append("plan_raisers", r)
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
		for k, v in (("propagation_plants", plants), ("propagation_cost", cost),
		             ("propagation_units", units), ("propagation_outside", outsiders)):
			doc.db_set(k, v, update_modified=False)
	return {"plan": doc.name, "raisers": len(kept), "plants": plants,
	        "cost": cost, "units": units, "outside": outsiders}


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
