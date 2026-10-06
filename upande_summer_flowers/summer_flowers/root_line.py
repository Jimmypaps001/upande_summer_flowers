# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The other way to raise a crop: grow a block, lift it once, plant the roots.

Eryngium can be had two ways, and they are not variations of each other.

    Option 1  TC -> propagation (6-8 weeks, hardening only) -> the farm.
              One plantlet, one plant. So the order is the whole requirement,
              and it is bought again every season.

    Option 2  TC -> hardening -> a MOTHER BLOCK in the field or a regulated
              area, grown for about a year to put roots on. Lift it once; one
              plant returns up to fifteen roots, and each root is a plant.
              Propagate the roots, plant them. What is not needed is stored and
              drawn on in a later season.

The difference is a factor of fifteen on the purchase, against a year of ground
and a block that is destroyed when it is lifted. Nothing in the planner could
say that before this: every route row carried a returns_per_plant field and no
code anywhere read it, so both options costed the same and option 2 looked like
a worse version of option 1.

This is deliberately NOT the motherstock engine. A motherstock stands and is cut
every week for its whole life, so what sizes it is the busiest week -- cuttings
cannot be banked. A lifted block is cut ONCE. Nothing about a peak week applies:
what matters is the total the season needs, the date the block has to go in to
be liftable in time, and what is already in store.
"""

import math

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

LIFT_STAGE = "Roots"


def lift_plan(plan, returns_per_plant=None, tc=None, root_item=None):
	"""What to buy, when to plant the block, when to lift it, and what is left over.

	`tc` overrides the quantity, the way it does everywhere else: somebody with a
	budget asks what a smaller block would leave them short.
	"""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	need = _plants_needed(plan)
	if not need["plants"]:
		return None

	rows = cp.route_rows(v) or []
	lift = next((r for r in rows if (r.get("stage") or "") == LIFT_STAGE), None)
	per_plant = flt(returns_per_plant if returns_per_plant not in (None, "")
	                else (lift and lift.get("returns_per_plant")) or 0)

	block_weeks = cint(lift and lift.get("weeks")) or cint(
		v.get("custom_sf_mother_block_weeks")) or 52
	prop_weeks = _weeks_after_lift(rows)
	harden = cint(v.get("hardening_weeks") or v.get("custom_sf_hardening_weeks"))
	lead = cint(v.supplier_lead_weeks)

	# Losses, taken where they happen: some of the block dies before it is
	# lifted, and some of the roots do not take in propagation.
	block_survival = 1.0 - flt(lift and lift.get("loss_pct") or 0) / 100.0
	root_survival = _survival_after_lift(rows)

	in_store = root_stock(plan, root_item)
	from_store = min(in_store["qty"], need["plants"])
	still_needed = max(0, need["plants"] - from_store)

	roots_to_raise = (int(math.ceil(still_needed / root_survival))
	                  if root_survival > 0 else still_needed)
	if per_plant > 0 and block_survival > 0:
		tc_needed = int(math.ceil(roots_to_raise / (per_plant * block_survival)))
	else:
		tc_needed = None

	buy = cint(tc) if cint(tc) else tc_needed
	roots_got = (int(buy * per_plant * block_survival)
	             if buy is not None and per_plant > 0 else 0)
	plants_got = int(roots_got * root_survival) + from_store
	spare_roots = max(0, roots_got - roots_to_raise)

	# Backwards from the first week the field wants a plant.
	first = getdate(need["first_planting"])
	lift_on = add_days(first, -7 * prop_weeks)
	block_in = add_days(lift_on, -7 * block_weeks)
	order_by = add_days(block_in, -7 * (lead + harden))

	return {
		"variety": plan.variety,
		"farm": plan.farm,
		"plants_needed": need["plants"],
		"first_planting": str(first),
		"returns_per_plant": per_plant,
		"block_weeks": block_weeks,
		"propagation_weeks": prop_weeks,
		"hardening_weeks": harden,
		"supplier_lead_weeks": lead,
		"block_survival_pct": round(block_survival * 100, 1),
		"root_survival_pct": round(root_survival * 100, 1),
		"in_store": in_store,
		"roots_from_store": cint(from_store),
		"plants_still_needed": cint(still_needed),
		"roots_to_raise": cint(roots_to_raise),
		"tc_needed": tc_needed,
		"tc": buy,
		"roots_at_lift": cint(roots_got),
		"plants_from_roots": cint(plants_got),
		"spare_roots_to_store": cint(spare_roots),
		"covers": buy is not None and plants_got >= need["plants"],
		"shortfall": max(0, need["plants"] - plants_got),
		"order_by": str(order_by),
		"block_planted": str(block_in),
		"lift_on": str(lift_on),
		"plants_ready": str(first),
		"one_off": True,
		"reason": None if per_plant > 0 else _(
			"This protocol says nothing about what a plant returns at lift, so "
			"the block cannot be sized. Set 'Returns per plant at lift' on the "
			"Roots row of the material route."),
	}


def _plants_needed(plan):
	"""What the field asks for, and the first week it asks."""
	plants, first = 0, None
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting):
			continue
		plants += cint(b.plants)
		d = b.get("planting_date")
		if d and (first is None or getdate(d) < getdate(first)):
			first = d
	return {"plants": plants, "first_planting": first}


def _weeks_after_lift(rows):
	"""Weeks between lifting the block and a plant being ready to go out."""
	seen, weeks = False, 0
	for r in rows:
		if (r.get("stage") or "") == LIFT_STAGE:
			seen = True
			continue
		if seen and (r.get("stage") or "") != "Plants":
			weeks += cint(r.get("weeks"))
	return weeks


def _survival_after_lift(rows):
	"""What fraction of lifted roots become plants in the ground."""
	seen, keep = False, 1.0
	for r in rows:
		if (r.get("stage") or "") == LIFT_STAGE:
			seen = True
			continue
		if seen:
			keep *= 1.0 - flt(r.get("loss_pct") or 0) / 100.0
	return keep


def root_stock(plan, item=None):
	"""Roots already in store, as real stock rather than a note on a plan.

	"Excess are stored to be used in future" only means anything if next
	season's order is netted against what is actually on the shelf, so this
	reads the Bin rather than a field somebody remembered to update.
	"""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	if not item:
		v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
		row = next((r for r in (cp.route_rows(v) or [])
		            if (r.get("stage") or "") == LIFT_STAGE), None)
		item = row and row.get("item")
	if not item or not frappe.db.exists("Item", item):
		return {"item": item, "qty": 0, "by_warehouse": [],
		        "note": _("No root item is named on the Roots row of the "
		                  "material route, so nothing in store can be counted "
		                  "against this plan.")}

	bins = frappe.get_all("Bin", filters={"item_code": item, "actual_qty": [">", 0]},
	                      fields=["warehouse", "actual_qty"])
	return {
		"item": item,
		"qty": cint(sum(flt(b.actual_qty) for b in bins)),
		"by_warehouse": [{"warehouse": b.warehouse, "qty": cint(b.actual_qty)}
		                 for b in bins],
		"note": None,
	}


def compare_ways(plan, returns_per_plant=None, split=None):
	"""The ways this crop can be got, costed against each other.

	Eryngium has no motherstock and the planner is right to refuse it one -- a
	motherstock stands and is cut weekly, and nothing here does. What it has
	instead is a choice of ways in, and the farm plans for TC, for roots, or for
	both at once. This says what each would cost for the season in hand.

	`split` is a dict of stage to percentage, so "both" is a real answer rather
	than a choice between two: {"TC": 80, "Roots": 20}.
	"""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp, sourcing

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	need = _plants_needed(plan)
	if not need["plants"]:
		return None
	rows = cp.route_rows(v) or []
	store = root_stock(plan)

	ways = []
	for w in sourcing.entry_ways(v) or []:
		per_unit = flt(w.get("plants_per_unit") or 0)
		if per_unit <= 0:
			per_unit = _plants_per_unit_from(rows, w.get("stage"))
		units = (int(math.ceil(need["plants"] / per_unit)) if per_unit > 0 else None)
		ways.append({
			"stage": w.get("stage"),
			"share_pct": flt(w.get("share_pct")),
			"plants_per_unit": per_unit,
			"units_for_whole_plan": units,
			"lead_weeks": cint(w.get("lead_weeks")),
			"weeks_to_ground": cint(w.get("weeks_to_ground")),
			"item": w.get("item"),
			"rate": flt(w.get("rate")),
			"cost": (units * flt(w.get("rate"))) if units and w.get("rate") else None,
		})

	# And the way that is not a purchase at all: grow the roots here.
	own = lift_plan(plan, returns_per_plant=returns_per_plant)

	if split:
		if isinstance(split, str):
			split = frappe.parse_json(split)
		for way in ways:
			pct = flt(split.get(way["stage"], way["share_pct"]))
			way["share_pct"] = pct
			way["units_at_share"] = (
				int(math.ceil(way["units_for_whole_plan"] * pct / 100.0))
				if way["units_for_whole_plan"] else None)

	return {
		"variety": plan.variety, "farm": plan.farm,
		"plants_needed": need["plants"],
		"first_planting": str(need["first_planting"] or ""),
		"roots_in_store": store,
		"ways": ways,
		"grow_your_own_roots": own,
		"has_motherstock": bool(sourcing.standing_stage(
			v, (sourcing.route_plan(v) or {}).get("entry_stage"))),
	}


def _plants_per_unit_from(rows, stage):
	"""Plants one bought unit becomes, walking the rows after it."""
	seen, out = False, 1.0
	for r in rows:
		st = r.get("stage") or ""
		if st == stage and not seen:
			seen = True
		if not seen:
			continue
		if st == "Plants":
			break
		lift = flt(r.get("returns_per_plant") or 0)
		out *= lift if lift > 0 else flt(r.get("yields_per_unit") or 1)
		out *= 1.0 - flt(r.get("loss_pct") or 0) / 100.0
	return out


def arrival_plan(plan, tc_share=None, returns_per_plant=None,
                 tc_qty=None, roots_qty=None):
	"""Both ways in, landing plants in the week the field wants them.

	The two ways are not alternatives running side by side at the same speed.
	Tissue culture is a few months. Roots are a year or more, because the block
	has to be grown before anything can be lifted off it. If both are to put
	plants in the ground in the SAME week -- and they must, because the demand
	does not care how a plant was raised -- then the root order goes in long
	before the tissue culture order. That is the whole scheduling problem rather
	than a detail of it, and nothing in the planner said it before.

	`tc_share` is the fraction of each week taken the short way, 0 to 1. The
	rest is grown through roots, where one root becomes `returns_per_plant`
	plants.
	"""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	rows = cp.route_rows(v) or []
	per_root = flt(returns_per_plant if returns_per_plant not in (None, "")
	               else _lift_return(rows) or 0)

	wanted = sum(cint(b.plants) for b in plan.plan_blocks
	             if cint(b.is_new_planting))
	# Quantities beat a percentage, because a percentage is not what anybody
	# buys. Ordering 300,000 plantlets and 40,000 roots is two orders placed at
	# two different times, and the share between them is a CONSEQUENCE of those
	# two figures rather than the thing that has to be chosen first.
	asked = {}
	if cint(tc_qty) or cint(roots_qty):
		from_tc = cint(tc_qty)
		from_roots = int(cint(roots_qty) * per_root) if per_root > 0 else 0
		covered = from_tc + from_roots
		share = (float(from_tc) / wanted) if wanted else 1.0
		share = max(0.0, min(1.0, share))
		asked = {
			"tc_qty": from_tc, "roots_qty": cint(roots_qty),
			"plants_from_tc": from_tc, "plants_from_roots": from_roots,
			"plants_covered": covered,
			"short_by": max(0, wanted - covered),
			"over_by": max(0, covered - wanted),
		}
	else:
		share = flt(tc_share if tc_share not in (None, "") else _tc_share(rows))

	tc_weeks = _weeks_for(rows, "TC")
	root_weeks = _weeks_for(rows, LIFT_STAGE)
	tc_lead = _lead_for(rows, "TC")
	root_lead = _lead_for(rows, LIFT_STAGE)

	weeks, tc_orders, root_orders = {}, {}, {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		land = _monday(b.planting_date)
		w = weeks.setdefault(land, {"plants": 0})
		w["plants"] += cint(b.plants)

	for land, w in weeks.items():
		by_tc = int(round(w["plants"] * share))
		by_root = w["plants"] - by_tc
		roots = int(math.ceil(by_root / per_root)) if per_root > 0 else None
		w.update({"by_tc": by_tc, "by_roots": by_root, "roots_needed": roots})
		# Each way counted back from the SAME landing week.
		w["tc_order_week"] = str(add_days(land, -7 * (tc_lead + tc_weeks)))
		w["root_order_week"] = (str(add_days(land, -7 * (root_lead + root_weeks)))
		                        if roots else None)
		w["week_iso"] = iso(land)
		w["tc_order_iso"] = iso(w["tc_order_week"])
		w["root_order_iso"] = iso(w["root_order_week"])
		if by_tc:
			tc_orders[w["tc_order_week"]] = tc_orders.get(w["tc_order_week"], 0) + by_tc
		if roots:
			root_orders[w["root_order_week"]] = root_orders.get(
				w["root_order_week"], 0) + roots

	ordered = sorted(weeks)
	# A share of the plan sent down a way that cannot say what a unit becomes is
	# not a plan, it is a blank. Say so, rather than printing a confident zero
	# beside "50% as roots".
	blocked = None
	if share < 1.0 and per_root <= 0:
		pct = round((1 - share) * 100, 1)
		# "Set the field" is the wrong instruction once the field IS set and the
		# plan is simply reading an older snapshot of it. A plan keeps the version
		# it was built on, which is the point of a snapshot -- so the fix is to
		# regenerate, and saying so saves somebody editing a protocol that is
		# already right and watching nothing change.
		newer = _newer_version_with_lift(v)
		if newer:
			blocked = _("{0}% of this plan is set to come through roots. The "
			            "protocol now says one root becomes {1} plants, but this "
			            "plan was built on {2} and keeps it. Regenerate the plan "
			            "against {3} to order the roots half."
			            ).format(pct, newer["returns"], v.name, newer["name"])
		else:
			blocked = _("{0}% of this plan is set to come through roots, but the "
			            "protocol does not say what a root becomes. Set 'Returns "
			            "per plant at lift' on the Roots row of the material "
			            "route and approve it — until then only the tissue "
			            "culture half can be ordered.").format(pct)
	return {
		"blocked": blocked,
		"asked": asked or None,
		"variety": plan.variety, "farm": plan.farm,
		"tc_share_pct": round(share * 100, 1),
		"returns_per_plant": per_root,
		"tc_weeks_to_ground": tc_weeks, "tc_lead_weeks": tc_lead,
		"root_weeks_to_ground": root_weeks, "root_lead_weeks": root_lead,
		"root_head_start_weeks": (root_lead + root_weeks) - (tc_lead + tc_weeks),
		"plants_total": sum(weeks[k]["plants"] for k in ordered),
		"tc_total": sum(weeks[k]["by_tc"] for k in ordered),
		"roots_total": sum(cint(weeks[k]["roots_needed"]) for k in ordered),
		"weeks": [dict(week=str(k), **weeks[k]) for k in ordered],
		# "tc" not "plantlets": the farm buys tissue culture, and calling it
		# something else on a purchase line is how a figure gets queried.
		"tc_order_schedule": [{"week": k, "week_iso": iso(k), "tc": v}
		                      for k, v in sorted(tc_orders.items())],
		"root_order_schedule": [{"week": k, "week_iso": iso(k), "roots": v}
		                        for k, v in sorted(root_orders.items())],
		# What the propagation unit is being asked to hand over, and when. This
		# is the thing the unit actually works to: not an order, a delivery.
		"propagation_request": [
			{"week": str(k), "week_iso": iso(k), "plants": weeks[k]["plants"],
			 "from_tc": weeks[k]["by_tc"], "from_roots": weeks[k]["by_roots"]}
			for k in ordered if weeks[k]["plants"]
		],
		# What the boxes should open on. An empty box asks somebody to invent a
		# number; the demand and the protocol's own split already imply one.
		"suggested": {
			"tc": sum(weeks[k]["by_tc"] for k in ordered),
			"roots": sum(cint(weeks[k]["roots_needed"]) for k in ordered),
			"from": _("the protocol's own split, {0}% tissue culture"
			          ).format(round(share * 100, 1)),
		},
		"roots_in_store": root_stock(plan),
	}


def _newer_version_with_lift(v):
	"""A later version of the same protocol that does carry a lift return."""
	rows = frappe.get_all(
		"Crop Protocol Version",
		filters={"variety": v.get("variety"), "farm": v.get("farm"),
		         "version": [">", cint(v.get("version"))]},
		fields=["name", "version"], order_by="version desc")
	for r in rows:
		got = frappe.db.get_value(
			"Crop Material Stage",
			{"parent": r.name, "stage": LIFT_STAGE,
			 "returns_per_plant": [">", 0]}, "returns_per_plant")
		if got:
			return {"name": r.name, "version": r.version, "returns": flt(got)}
	return None


def _monday(d):
	d = getdate(d)
	return add_days(d, -d.weekday())


def iso(d):
	"""2027-W06. The farm plans in weeks and reads dates back into them, so the
	label is carried beside every date rather than worked out by eye."""
	if not d:
		return None
	y, w, _dow = getdate(d).isocalendar()
	return "%d-W%02d" % (y, w)


def _lift_return(rows):
	for r in rows:
		if (r.get("stage") or "") == LIFT_STAGE:
			return flt(r.get("returns_per_plant") or 0)
	return 0


def _tc_share(rows):
	"""The split the protocol already states, as a fraction."""
	tc = sum(flt(r.get("share_pct") or 0) for r in rows
	         if (r.get("stage") or "") == "TC")
	root = sum(flt(r.get("share_pct") or 0) for r in rows
	           if (r.get("stage") or "") == LIFT_STAGE)
	return (tc / (tc + root)) if (tc + root) else 1.0


def _weeks_for(rows, stage):
	"""Weeks from that stage arriving to a plant standing in the field."""
	seen, weeks = False, 0
	for r in rows:
		st = r.get("stage") or ""
		if st == stage and not seen:
			seen = True
		if not seen:
			continue
		if st == "Plants":
			break
		weeks += cint(r.get("weeks"))
	return weeks


def _lead_for(rows, stage):
	for r in rows:
		if (r.get("stage") or "") == stage:
			return cint(r.get("lead_weeks"))
	return 0


@frappe.whitelist()
def how_it_is_raised(production_plan):
	"""Whether this crop has a motherstock, for the form to offer the right thing.

	Cheap on purpose: the form asks it on every refresh, and all it needs is
	which of two worlds the variety lives in.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	from upande_summer_flowers.summer_flowers import sourcing

	protocol = frappe.db.get_value("Summer Flower Production Plan",
	                               production_plan, "protocol")
	if not protocol:
		return {"motherstock": False, "reason": None}
	v = frappe.get_cached_doc("Crop Protocol Version", protocol)
	decided = sourcing.route_plan(v) or {}
	stage = decided.get("entry_stage")
	return {
		"motherstock": bool(stage and sourcing.standing_stage(v, stage)),
		"entry_stage": stage,
		"reason": decided.get("reason"),
	}


@frappe.whitelist()
def ways_for(production_plan, returns_per_plant=None, split=None):
	"""What the dashboard draws for a crop that has no motherstock."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	return compare_ways(plan, returns_per_plant=returns_per_plant, split=split)


@frappe.whitelist()
def arrivals_for(production_plan, tc_share=None, returns_per_plant=None,
                 tc_qty=None, roots_qty=None):
	"""Both ways landing in the same week, for the dashboard."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	return arrival_plan(plan, tc_share=tc_share,
	                    returns_per_plant=returns_per_plant,
	                    tc_qty=tc_qty, roots_qty=roots_qty)


@frappe.whitelist()
def plan_for(production_plan, returns_per_plant=None, tc=None):
	"""What the dashboard draws for a crop raised through its own roots."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	return lift_plan(plan, returns_per_plant=returns_per_plant, tc=tc)
