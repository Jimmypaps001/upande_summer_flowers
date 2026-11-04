# Copyright (c) 2026, James Kiruga and contributors
"""Roots go in by the trough, so the plan has to ask for them by the trough."""

from frappe.utils import cint, flt


def roots_for(capacity_plants, plants_per_root):
	"""Roots that fill a trough of this size. Whole roots; a part root is not"""
	per = flt(plants_per_root)
	return int(cint(capacity_plants) / per) if per > 0 else 0


def deliveries_of(capacity_plants, lifts):
	"""What one trough of this size hands over, as {weeks after setting: plants}."""
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
	"""What a trough set at `at` actually covers, as [(week, plants)]."""
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
	"""Which troughs to set, and when, for a season's root share."""
	left = {k: cint(v) for k, v in (wanted or {}).items() if cint(v) > 0}
	order = [k for k in (weeks_order or []) if k in wanted]
	caps = sorted((cint(c) for c in (capacities or []) if cint(c) > 0), reverse=True)
	if not left or not order or not caps:
		return {"settings": [], "delivered": {}, "roots_total": 0,
		        "plants_total": 0, "troughs_used": 0}

	# What the root share can pay for. Rounded DOWN to whole troughs on purpose:
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
