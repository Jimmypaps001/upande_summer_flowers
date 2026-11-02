# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Roots go in by the trough, so the plan has to ask for them by the trough.

Demand is smooth and troughs are not. A trough is planted full or not at all --
7,000 plants' worth of roots at Kariki Molo, 9,000 in the big ones -- and it
pays that back over the weeks its lift table names, not in the week it was set.
One Eryngium trough set in week W delivers 45% of itself in W+16, 30% in W+18
and 25% in W+20.

Sizing roots against a single week's demand therefore asks for quantities no
propagator can plant: 1,140 roots here, 380 there, a part trough every time.
The farm's answer is the other way round -- set whole troughs, and buy tissue
culture, which comes in any quantity on a fifteen-week lead, to cover whatever
the troughs have not.

So this chooses troughs. It never sets a part trough and it never sets more
than the root share can pay for: what it cannot cover is left for tissue
culture, which is the side that bends.

Nothing here reads the database. It is given the weeks, the lift table and the
capacities, and it is the one place the arithmetic lives.
"""

from frappe.utils import cint, flt


def roots_for(capacity_plants, plants_per_root):
	"""Roots that fill a trough of this size. Whole roots; a part root is not
	a thing you can plant, and rounding one up overfills the trough."""
	per = flt(plants_per_root)
	return int(cint(capacity_plants) / per) if per > 0 else 0


def deliveries_of(capacity_plants, lifts):
	"""What one trough of this size hands over, as {weeks after setting: plants}.

	The last lift takes the remainder rather than its own percentage, so the
	lifts add to the trough rather than to a rounding of it.
	"""
	sched = sorted(lifts or [], key=lambda L: cint(L.get("weeks_after")))
	if not sched:
		return {}
	cap, out, used = cint(capacity_plants), {}, 0
	for i, L in enumerate(sched):
		qty = (cap - used) if i == len(sched) - 1 else int(
			round(cap * flt(L.get("share_pct") or 0) / 100.0))
		used += qty
		after = cint(L.get("weeks_after"))
		out[after] = out.get(after, 0) + qty
	return out


def _serve(stream, at, order, left):
	"""What a trough set at `at` actually covers, as [(week, plants)].

	A lift is not spent in the week it is taken. A week planting 800 plants
	cannot absorb a lift of 3,150, and the 2,350 left over are not waste -- the
	trough is still standing, and the weeks after it are cut from the same one
	until it is drawn down. Counting only the landing week threw three quarters
	of every trough away and made whole troughs look like a bad bargain.

	A lift serves from its own week forward, never backward: the plants are not
	there before they are lifted.
	"""
	out, j = [], 0
	for after in sorted(stream):
		qty = stream[after]
		j = max(j, at + after)
		k = j
		while qty > 0 and k < len(order):
			take = min(qty, left.get(order[k], 0) - sum(
				n for w, n in out if w == order[k]))
			if take > 0:
				out.append((order[k], take))
				qty -= take
			k += 1
		j = max(j, at + after)
	return out


def plan_troughs(wanted, weeks_order, lifts, capacities, plants_per_root,
                 budget_plants=None):
	"""Which troughs to set, and when, for a season's root share.

	`wanted` is {week: plants this week would like from roots}; `weeks_order`
	is those weeks in order, which is also the order a trough may be set in.
	`capacities` is the troughs the unit actually has, in plants, and each may
	be used once -- a bench does not grow more troughs because a plan asked it
	to.

	Troughs are placed one at a time, each where it does the most good: the
	week whose lift stream covers the most of what is still wanted. A trough
	that would cover nothing is not set, which is what stops the last few
	troughs being spent on a tail of weeks that only wanted a hundred plants.

	Returns the settings, what they deliver week by week, and what is left for
	tissue culture to cover.
	"""
	left = {k: cint(v) for k, v in (wanted or {}).items() if cint(v) > 0}
	order = [k for k in (weeks_order or []) if k in wanted]
	caps = sorted((cint(c) for c in (capacities or []) if cint(c) > 0), reverse=True)
	if not left or not order or not caps:
		return {"settings": [], "delivered": {}, "roots_total": 0,
		        "plants_total": 0, "troughs_used": 0}

	# What the root share can pay for. Rounded DOWN to whole troughs on purpose:
	# tissue culture can be bought in small quantities to meet a week, roots
	# cannot, so the side that cannot bend is the side that stops short.
	budget = cint(budget_plants) if budget_plants is not None else sum(left.values())

	settings, delivered, spent = [], {}, 0
	for cap in caps:
		if spent + cap > budget:
			continue
		stream = deliveries_of(cap, lifts)
		if not stream:
			break
		best, best_fit = None, 0
		for i, _set_week in enumerate(order):
			fit = sum(n for _w, n in _serve(stream, i, order, left))
			if fit > best_fit:
				best, best_fit = i, fit
		if best is None or best_fit <= 0:
			# Nothing left that a whole trough of this size can usefully serve.
			continue
		rows = {}
		for wk, qty in _serve(stream, best, order, left):
			rows[wk] = rows.get(wk, 0) + qty
			delivered[wk] = delivered.get(wk, 0) + qty
			left[wk] = max(0, left.get(wk, 0) - qty)
		settings.append({
			"set_week": order[best],
			"capacity_plants": cap,
			"roots": roots_for(cap, plants_per_root),
			"deliveries": [{"week": w, "plants": p} for w, p in sorted(rows.items())],
		})
		spent += cap

	return {
		"settings": settings,
		"delivered": delivered,
		"roots_total": sum(s["roots"] for s in settings),
		"plants_total": spent,
		"troughs_used": len(settings),
	}
