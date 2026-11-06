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

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

DEFAULT_FORM = "Tissue Culture"


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
	mine = [r for r in rows if supplier and r.source == supplier]
	if not mine:
		mine = [r for r in rows if not r.source]
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
	return {"form": form, "default_cycles": stated_cycles(None, form),
	        "suppliers": out,
	        # Any supplier can be used; these are only the ones with a profile
	        # on file. The rest start from the default and are edited on screen.
	        "others": frappe.get_all("Supplier", filters={"disabled": 0},
	                                 pluck="name", order_by="name", limit=400)}


def _wanted_by_week(plan):
	"""{Monday of the planting week: plants that have to go in the ground}."""
	out = {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		key = _monday(b.planting_date)
		out[key] = out.get(key, 0) + (cint(b.get("plants_to_plant")) or cint(b.plants))
	return dict(sorted(out.items()))


def _normalised(picks, form):
	"""The chosen suppliers, their share of the season and their cycles."""
	out = []
	for p in picks or []:
		name = (p.get("supplier") or "").strip()
		if not name:
			continue
		cycles = [c for c in (p.get("cycles") or [])
		          if flt(c.get("share_pct")) > 0]
		if not cycles:
			cycles = stated_cycles(name, form) or [
				{"lift_no": 1, "weeks_after": 0, "share_pct": 100.0}]
		cycles = sorted(cycles, key=lambda c: cint(c.get("weeks_after")))
		total = sum(flt(c["share_pct"]) for c in cycles) or 100.0
		first = cint(cycles[0].get("weeks_after"))
		out.append({
			"supplier": name,
			"share_pct": flt(p.get("share_pct")),
			"lead_weeks": cint(p.get("lead_weeks")),
			# Offsets from the FIRST cycle: the order is timed so that cycle one
			# lands in the week the field wants the plants, and the rest follow.
			"cycles": [{"lift_no": cint(c.get("lift_no")) or i + 1,
			            "weeks_after": cint(c.get("weeks_after")),
			            "offset": cint(c.get("weeks_after")) - first,
			            "share": flt(c["share_pct"]) / total,
			            "share_pct": round(flt(c["share_pct"]) * 100.0 / total, 4)}
			           for i, c in enumerate(cycles)],
		})
	share = sum(flt(x["share_pct"]) for x in out)
	if out and share <= 0:
		for x in out:
			x["share_pct"] = 100.0 / len(out)
	elif out and abs(share - 100.0) > 0.01:
		for x in out:
			x["share_pct"] = flt(x["share_pct"]) * 100.0 / share
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

	chosen = _normalised(picks, form)
	if not chosen:
		return {"plan": plan, "form": form, "entry_stage": entry,
		        "suppliers": [], "weeks": [],
		        "reason": _("Choose at least one supplier to try.")}

	wanted = _wanted_by_week(doc)
	if not wanted:
		return {"plan": plan, "form": form, "entry_stage": entry,
		        "suppliers": chosen, "weeks": [],
		        "reason": _("This plan has no new plantings to supply.")}

	# One unit bought becomes this many plants in the ground, losses and all.
	per_unit = 1.0 / (flt(v.cuttings_per_plant_required) or 1.0)

	# Walk the planting weeks forward. What an earlier consignment's later
	# cycle lands is this week's stock, so it is spent before anything else is
	# ordered -- which is the whole point of knowing a supplier's cycles.
	landing = {}
	rows, orders = [], []
	carry = []          # [(week it landed, plants still in hand)]
	short_total = bought_total = 0
	held_max = 0
	for when, want in wanted.items():
		# Everything that has landed by now is in hand, whether or not it landed
		# in a week anybody was planting. Plants from an earlier consignment's
		# later cycle wait on the bench until the next planting week wants them.
		came = 0
		for at in sorted([k for k in landing if getdate(k) <= getdate(when)]):
			got = cint(landing.pop(at))
			came += got
			carry.append((at, got))
		in_hand = sum(q for _at, q in carry)
		take = min(in_hand, want)
		# Oldest first: material does not improve with keeping.
		left, waited = take, 0
		while left > 0 and carry:
			at, q = carry[0]
			use = min(q, left)
			waited = max(waited, (getdate(when) - getdate(at)).days // 7)
			left -= use
			if use >= q:
				carry.pop(0)
			else:
				carry[0] = (at, q - use)
		held_max = max(held_max, waited)
		need = max(0, want - take)
		buy_here = []
		for s_ in chosen:
			part = int(round(need * flt(s_["share_pct"]) / 100.0))
			if not part:
				continue
			first = s_["cycles"][0]["share"] or 1.0
			# Enough that the FIRST cycle covers this week's share of the need.
			units = int(round(part / first / (per_unit or 1.0)))
			plants = units * per_unit
			order_week = add_days(when, -7 * cint(s_["lead_weeks"]))
			lands = []
			for c in s_["cycles"]:
				got = int(round(plants * c["share"]))
				at = add_days(when, 7 * c["offset"])
				lands.append({"lift": c["lift_no"], "share_pct": c["share_pct"],
				              "week_iso": _iso(at), "plants": got,
				              "after_the_week": 1 if c["offset"] else 0})
				if c["offset"]:
					landing[at] = cint(landing.get(at, 0)) + got
			buy_here.append({
				"supplier": s_["supplier"], "units": units,
				"plants": int(round(plants)),
				"order_iso": _iso(order_week), "order_date": str(order_week),
				"order_passed": 1 if getdate(order_week) < getdate(nowdate()) else 0,
				"lands": lands,
			})
			bought_total += units
			orders.append({"supplier": s_["supplier"], "for_iso": _iso(when),
			               "order_iso": _iso(order_week), "units": units})
		got_now = take + sum(
			l["plants"] for b in buy_here for l in b["lands"]
			if not l["after_the_week"])
		short = max(0, want - got_now)
		short_total += short
		rows.append({
			"week_iso": _iso(when), "week_start": str(when),
			"wanted": want,
			"carried_in": came,
			"used_from_carry": take,
			"held_weeks": waited,
			"bought": sum(b["units"] for b in buy_here),
			"lands_now": got_now,
			"planted": min(want, got_now),
			"short": short,
			# What is still on the bench after this week, and what is still to
			# land from consignments already ordered.
			"carry_out": sum(q for _at, q in carry),
			"to_land": sum(cint(x) for x in landing.values()),
			"buys": buy_here,
		})

	# Anything that lands after the last planting week is bought and not used.
	stranded = sum(cint(x) for x in landing.values()) + sum(q for _at, q in carry)
	by_supplier = {}
	for o in orders:
		b = by_supplier.setdefault(o["supplier"], {"supplier": o["supplier"],
		                                           "units": 0, "orders": 0,
		                                           "first_order": None})
		b["units"] += cint(o["units"])
		b["orders"] += 1
		if not b["first_order"] or o["order_iso"] < b["first_order"]:
			b["first_order"] = o["order_iso"]

	wanted_total = sum(wanted.values())
	return {
		"plan": plan, "form": form, "entry_stage": entry,
		"suppliers": [dict(s, **by_supplier.get(s["supplier"], {})) for s in chosen],
		"weeks": rows,
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
	}
