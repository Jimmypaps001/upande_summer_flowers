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

from upande_summer_flowers.summer_flowers.planning import season_year
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

LIFT_STAGE = "Roots"

# Where a split came from, said in the card's own words.
SPLIT_FROM = {
	"protocol": _("the protocol's own split"),
	"agreed": _("the pair already agreed on this plan"),
	"quantities": _("the quantities in the boxes"),
	"asked": _("the share you asked for"),
	"default": _("an even split — the protocol does not say how this crop divides"),
}


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
	"""Plants one bought unit becomes, walking the rows after it.

	Planting out is part of the walk. It multiplies nothing, but a share of
	what goes in the ground fails to establish, and stopping before the end
	stage skipped that loss on every route that carries it there.
	"""
	seen, out = False, 1.0
	for r in rows:
		st = r.get("stage") or ""
		if st == stage and not seen:
			seen = True
		if not seen:
			continue
		if st == "Plants":
			out *= 1.0 - flt(r.get("loss_pct") or 0) / 100.0
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
	# An agreed pair is what the card should open on, the way an agreed
	# motherstock line is. Nothing typed and something committed means show what
	# was committed, not a fresh suggestion beside it.
	#
	# Unless the plan has been rebuilt since it was agreed and no longer needs
	# that many plants. Opening on the old pair would then have the card suggest
	# an order already known to be wrong -- and worse, derive the split from it,
	# so a plan needing fewer plants than were agreed as tissue culture reads as
	# 100% tissue culture and no roots at all. What was agreed is still returned,
	# so the card can say what it was.
	# A share that was explicitly asked for is somebody trying a different
	# split, so it beats the agreed pair the same way a typed quantity does.
	# Without this the agreement was adopted first and the share derived back
	# off it, so every share typed into the card returned the agreement's own
	# figure unchanged -- the box moved and nothing else did.
	if not (cint(tc_qty) or cint(roots_qty)) and tc_share in (None, "") \
			and cint(plan.get("tc_choice_committed")) \
			and not _agreement_stale(plan, wanted, per_root):
		tc_qty = cint(plan.get("tc_plants_committed"))
		roots_qty = cint(plan.get("roots_committed"))

	# What is expected to reach the field out of what is ordered. A provision,
	# sitting at zero until somebody sets it: ordering a plant for every plant
	# wanted is a decision about losses, not the absence of one.
	keep = _survival("Tissue Culture", plan.get("protocol"))
	keep_roots = _survival("Roots", plan.get("protocol"))

	asked = {}
	# Where the split came from, because the card has been calling all three
	# "the protocol's own split" -- including 44.3% derived from an agreed pair
	# on a protocol that states 82.18%, which is the sort of sentence that
	# sends somebody to the protocol looking for a number that was never there.
	# A crop bought one way has no second way for the rest to come down. What is
	# not covered by the one way in is SHORT, and calling it roots told a farm
	# buying 4,000 plants of a 6,000-plant plan that a third of its season was
	# coming through roots -- off a route with no roots in it, and then that the
	# protocol would not say what those roots become.
	ways = _ways_in(v)
	one_way = len(ways) < 2

	origin = "protocol"
	if cint(tc_qty) or cint(roots_qty):
		from_tc = int(cint(tc_qty) * keep)
		from_roots = (int(cint(roots_qty) * per_root * keep_roots)
		              if per_root > 0 else 0)
		covered = from_tc + from_roots
		share = (1.0 if one_way
		         else max(0.0, min(1.0, (float(from_tc) / wanted) if wanted else 1.0)))
		asked = {
			"tc_qty": from_tc, "roots_qty": cint(roots_qty),
			"plants_from_tc": from_tc, "plants_from_roots": from_roots,
			"plants_covered": covered,
			"short_by": max(0, wanted - covered),
			"over_by": max(0, covered - wanted),
		}
		origin = "agreed" if cint(plan.get("tc_choice_committed")) else "quantities"
	elif tc_share not in (None, ""):
		share = 1.0 if one_way else flt(tc_share)
		origin = "asked"
	else:
		share = 1.0 if one_way else _tc_share(rows)
		origin = ("single" if one_way else
		          "protocol" if _states_a_split(rows) else "default")

	# Asked of the stages this crop is actually bought at. The card was written
	# for Eryngium and put these four questions to "TC" and "Roots" by name, so a
	# crop bought as seed -- whose entry stage is Seeds -- was told it had no
	# supplier lead and no time to the ground, and the tile read "0 wk lead + 0 wk
	# to ground" over a route that plainly states thirteen and five. The labels
	# beside them have come from the route since _ways_in was written; only the
	# numbers were still somebody else's crop.
	primary = ways[0] if ways else "TC"
	secondary = ways[1] if len(ways) > 1 else LIFT_STAGE
	tc_weeks = _weeks_for(rows, primary)
	root_weeks = _weeks_for(rows, secondary)
	tc_lead = _lead_for(rows, primary)
	root_lead = _lead_for(rows, secondary)

	weeks, tc_orders, root_pulls = {}, {}, {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		land = _monday(b.planting_date)
		w = weeks.setdefault(land, {"plants": 0})
		w["plants"] += cint(b.plants)

	# Where a quantity has been asked for, the weeks must add up to exactly what
	# that quantity DELIVERS. Rounding each week on its own lost two plantlets
	# out of 851,961 and the card then carried two different figures for one
	# order -- the box said what was agreed and the tile said what the weeks
	# came to.
	#
	# Plants, not plantlets. This used to apportion the order itself, so 470,112
	# plantlets filled 470,112 plants' worth of weeks and the suggestion then
	# grossed that up for losses a second time: an agreed order of 470,112 read
	# back as 530,003. It was invisible while the loss provision sat at zero,
	# because then the two are the same number.
	share_by_week = _apportion(
		weeks, share, cint(asked["plants_from_tc"]) if asked else None)

	# What one bought unit of the first way becomes, and what survives the first
	# loss on the way there. Worked out once: it is the same walk every week.
	per_unit_one = _plants_per_unit_from(rows, primary)
	account = _loss_account(v, rows, primary)
	# Germination is the first of the two percentages the protocol states, and
	# it only means anything where the route actually raises what it buys. A
	# crop bought as plants loses nothing to rooting, and a column saying so
	# would be a column of the same number twice.
	first_pct = (flt(account["stated"]["rooting_success_pct"])
	             if account.get("rooting_applies") else 0.0)
	# And the second: what fails to establish once it is planted out. Named
	# separately from the first because they are different events -- one happens
	# on the bench, one in the field -- and a reader looking at 14,286 becoming
	# 10,000 is entitled to be told which of them took the 4,286.
	second_pct = (flt(account["stated"]["field_establishment_pct"])
	              if account.get("establishment_applies") else 0.0)
	for land, w in weeks.items():
		by_tc = share_by_week[land]
		# On a one-way crop the rest of the week is not a second way in, it is
		# simply not bought. Leaving it here made the Total column add up to the
		# week's demand however little was ordered.
		by_root = 0 if one_way else w["plants"] - by_tc
		roots = int(math.ceil(by_root / per_root)) if (per_root > 0 and by_root) else None
		# The week's own order, and what is left of it after each loss. The
		# table used to print the PLANTS under the name of the thing bought, so
		# a week needing 40,000 plants read "40,000 seeds" on a protocol that
		# loses 44% of them. Rounded up a week at a time, which is how the
		# procurement plan rounds, so the two agree to the seed.
		units_one = (int(math.ceil(by_tc / per_unit_one))
		             if (by_tc and per_unit_one > 0) else 0)
		# The week as a ladder down from what is bought to what is left
		# standing. Planting is not the end of it: a plant is put in the ground
		# and SOME of them take, so what goes in is more than what the plan
		# needs -- 14,286 planted to be left with 10,000 -- and the table has to
		# say which number is which or it reads as though the plan asked for
		# exactly what it buys.
		# What reaches planting out. Where nothing is raised -- plants bought
		# ready, tubers straight out -- that is what was bought.
		to_plant = (int(round(units_one * first_pct / 100.0))
		            if first_pct else units_one)
		w.update({"by_tc": by_tc, "by_roots": by_root, "roots_needed": roots,
		          "units_tc": units_one,
		          # kept under the old name: what reaches planting out
		          "germinated_tc": to_plant,
		          "to_plant_tc": to_plant,
		          "lost_germinating_tc": (max(0, units_one - to_plant)
		                                  if to_plant else 0),
		          "lost_establishing_tc": (max(0, to_plant - by_tc)
		                                   if to_plant else 0)})
		# Each way counted back from the SAME landing week.
		#
		# Tissue culture is ordered for the week it feeds: a lab sends batch
		# after batch. Roots are not. Roots come in ONCE, go into the coldroom,
		# and are taken out week by week as the plantings need them -- early
		# enough that what they become lands with the tissue culture. So what a
		# week has against it is not an order, it is a withdrawal from store.
		w["tc_order_week"] = str(add_days(land, -7 * (tc_lead + tc_weeks)))
		w["root_out_week"] = (str(add_days(land, -7 * root_weeks))
		                      if roots else None)
		w["week_iso"] = iso(land)
		w["tc_order_iso"] = iso(w["tc_order_week"])
		w["root_out_iso"] = iso(w["root_out_week"])
		if by_tc:
			tc_orders[w["tc_order_week"]] = tc_orders.get(w["tc_order_week"], 0) + by_tc
		if roots:
			root_pulls[w["root_out_week"]] = root_pulls.get(
				w["root_out_week"], 0) + roots

	ordered = sorted(weeks)
	# A share of the plan sent down a way that cannot say what a unit becomes is
	# not a plan, it is a blank. Say so, rather than printing a confident zero
	# beside "50% as roots".
	blocked = None
	if not one_way and share < 1.0 and per_root <= 0:
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
	# What this crop is ACTUALLY bought as. The card was written for Eryngium,
	# which comes as tissue culture and as roots, and those two names were
	# written into it -- so a crop bought as plants, as seed or as tubers read
	# as tissue culture on the dashboard. The figures were always its own; the
	# labels were somebody else's.
	ways = _ways_in(v)
	return {
		"blocked": blocked,
		"asked": asked or None,
		"ways": len(ways),
		"route_summary": v.get("route_summary"),
		# Who raises each way in, beside who sells it. Choosing a lab answers
		# half the question; the other half -- who sticks what the lab sends, and
		# who roots the plant roots -- was a button away on another document.
		"raised_by": _raised_by(plan),
		"primary_stage": ways[0] if ways else "TC",
		"secondary_stage": ways[1] if len(ways) > 1 else None,
		# What was agreed, if anything was. A saved plan that opens as a blank
		# card reads as a plan that was never saved, and the next thing somebody
		# does is save it again -- so the agreement comes back with the figures.
		"committed": _committed(plan),
		"variety": plan.variety, "farm": plan.farm,
		"tc_share_pct": round(share * 100, 1),
		"returns_per_plant": per_root,
		"tc_weeks_to_ground": tc_weeks, "tc_lead_weeks": tc_lead,
		"root_weeks_to_ground": root_weeks, "root_lead_weeks": root_lead,
		"root_head_start_weeks": (root_lead + root_weeks) - (tc_lead + tc_weeks),
		"plants_total": sum(weeks[k]["plants"] for k in ordered),
		"tc_total": sum(weeks[k]["by_tc"] for k in ordered),
		# Plants are what the field wants; units are what has to be bought to
		# stand them up. They are the same number only while the losses are zero,
		# which is how a tile reading "1,187,200 Seeds" for 1,187,200 plants went
		# unnoticed on a protocol that loses 44% of them before planting.
		# Summed off the weeks rather than divided out of the season total: the
		# order is placed a week at a time and each week rounds up, so the season
		# figure is the sum of those roundings. Dividing once reported five seeds
		# fewer than the procurement plan went on to order.
		"tc_units_total": (sum(cint(weeks[k].get("units_tc")) for k in ordered)
		                   if _units_for(1, rows, primary) is not None
		                   else None),
		"tc_germinated_total": sum(cint(weeks[k].get("germinated_tc"))
		                           for k in ordered),
		"tc_first_loss_pct": first_pct,
		"tc_second_loss_pct": second_pct,
		"tc_lost_establishing_total": sum(cint(weeks[k].get("lost_establishing_tc"))
		                                  for k in ordered),
		"tc_lost_germinating_total": sum(cint(weeks[k].get("lost_germinating_tc"))
		                                 for k in ordered),
		"tc_to_plant_total": sum(cint(weeks[k].get("to_plant_tc"))
		                         for k in ordered),
		"tc_plants_per_unit": round(_plants_per_unit_from(rows, primary), 6),
		# How the order got bigger than the planting. The card could already be
		# read two ways -- a reader who knew the protocol saw the gross-up and a
		# reader who did not saw a number that did not match the plantings -- and
		# the figures that explain it were a tab away on another document.
		"losses": account,
		"roots_total": sum(cint(weeks[k]["roots_needed"]) for k in ordered),
		"weeks": [dict(week=str(k), **weeks[k]) for k in ordered],
		# "tc" not "plantlets": the farm buys tissue culture, and calling it
		# something else on a purchase line is how a figure gets queried.
		"tc_order_schedule": [{"week": k, "week_iso": iso(k), "tc": v}
		                      for k, v in sorted(tc_orders.items())],
		# One line, not fifty. The whole season's roots are bought in one
		# consignment, placed a lead time before the first week any of them has
		# to come out of store.
		"root_order_schedule": _root_order(root_pulls, root_lead),
		# Out of store in trough-fulls, lifted out to the weeks that want them.
		"root_batches": _root_batches(
			weeks, ordered, per_root, root_weeks,
			# The ROOTS lifts. A protocol carries a lift table per form, and
			# mixing them gave a trough five lifts across two different crops'
			# worth of timings.
			[{"weeks_after": cint(L.weeks_after_arrival), "share_pct": flt(L.share_pct)}
			 for L in (v.get("lifts") or [])
			 if (L.form or "").strip().lower().startswith("root")]),

		# And what comes back out of the coldroom, week by week.
		"root_withdrawals": [{"week": k, "week_iso": iso(k), "roots": v}
		                     for k, v in sorted(root_pulls.items())],
		"roots_stored": sum(root_pulls.values()),
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
			# Grossed up by the loss provision: what has to be ORDERED for that
			# many plants to arrive, which is not the same number the moment the
			# provision is anything but zero.
			"tc": int(math.ceil(sum(weeks[k]["by_tc"] for k in ordered) / keep)),
			"roots": int(math.ceil(
				sum(cint(weeks[k]["roots_needed"]) for k in ordered) / keep_roots)),
			"from": _("{0}, {1}% tissue culture").format(
				SPLIT_FROM.get(origin, SPLIT_FROM["protocol"]),
				round(share * 100, 1))
			+ (_(", grossed up for losses of {0}% on tissue culture and {1}% "
			     "on roots").format(round((1 - keep) * 100, 1),
			                        round((1 - keep_roots) * 100, 1))
			   if (keep < 1 or keep_roots < 1) else ""),
		},
		"split_from": origin,
		"survival_pct": round(keep * 100, 2),
		"roots_survival_pct": round(keep_roots * 100, 2),
		"roots_in_store": root_stock(plan),
		# What the whole exercise is for. Ordering material is a means; the
		# question is whether the season's stems arrive when the market wants
		# them, and the plan already knows both figures week by week.
		"demand_vs_production": _demand_vs_production(plan),
		# The tolerance the planting programme was solved against, so the table
		# judges a week by the same rule the plan was built on rather than by
		# whether it hit the demand exactly.
		"variance_band_pct": flt(plan.get("weekly_variance_band_pct")) or 10.0,
		"weeks_outside_band": cint(plan.get("weeks_outside_band")),
	}


def _agreed_plants(plan, per_root):
	"""Plants the committed pair covers: the tissue culture plus what roots give."""
	return cint(plan.get("tc_plants_committed")) + int(
		cint(plan.get("roots_committed")) * flt(per_root))


def _agreement_stale(plan, wanted, per_root):
	"""Has the plan moved away from what was agreed as its material?

	One rule, in one place: the card opens on an agreement only while it still
	covers the plan, and says so when it does not.
	"""
	agreed = _agreed_plants(plan, per_root)
	if not agreed or not wanted:
		return False
	return abs(agreed - wanted) > max(1, wanted * 0.01)


def _committed(plan):
	"""The agreement already written to this plan, or nothing.

	The pair of quantities, who was to send the tissue culture, and the two
	documents the agreement built. All of it was on screen the moment it was
	saved and none of it survived a refresh, which is the one moment a planner
	most wants to see it again.
	"""
	if not cint(plan.get("tc_choice_committed")):
		return None
	raw = plan.get("tc_supplier_allocations")
	rows = []
	if raw:
		try:
			rows = frappe.parse_json(raw) or []
		except Exception:
			# A hand-edited field should not take the whole card down with it.
			rows = []
		if not isinstance(rows, list):
			rows = []
	# What was agreed covered the plan as it stood. A plan can be rebuilt after
	# that -- on a new protocol, or against a tolerance that changes how much is
	# planted -- and then the order on file is for a season that no longer
	# exists. The card can only say so if it is told both numbers.
	wanted = sum(cint(b.plants) for b in plan.plan_blocks
	             if cint(b.is_new_planting))
	from upande_summer_flowers.summer_flowers import crop_protocol as cp
	per_root = flt(_lift_return(cp.route_rows(
		frappe.get_cached_doc("Crop Protocol Version", plan.protocol)) or []) or 0)
	return {
		"tc": cint(plan.get("tc_plants_committed")),
		"roots": cint(plan.get("roots_committed")),
		"order_by": str(plan.get("tc_order_date_committed") or ""),
		"plants_agreed": _agreed_plants(plan, per_root),
		"plants_now": wanted,
		"stale": _agreement_stale(plan, wanted, per_root),
		"allocations": rows,
		"procurement_plan": frappe.db.get_value(
			"Summer Flower Procurement Plan", {"production_plan": plan.name},
			"name", order_by="creation desc"),
		"propagation_plan": frappe.db.get_value(
			"Summer Flower Propagation Plan",
			{"variety": plan.variety,
			 "season_start_year": season_year(plan.season_start_year),
			 "status": ["!=", "Rejected"]}, "name", order_by="creation desc"),
	}


def _apportion(weeks, share, exact=None):
	"""Tissue culture per week: proportional, and adding to the total exactly.

	Largest remainder. Each week takes its whole share, then the plants left over
	by the rounding go to the weeks with the largest fractions -- so the parts
	sum to the whole instead of to the whole give or take a rounding error.
	"""
	keys = sorted(weeks)
	if exact is None:
		return {k: int(round(weeks[k]["plants"] * share)) for k in keys}
	total = sum(weeks[k]["plants"] for k in keys)
	if not total:
		return {k: 0 for k in keys}
	want = min(cint(exact), total)
	out, rema = {}, []
	for k in keys:
		raw = weeks[k]["plants"] * want / float(total)
		out[k] = int(raw)
		rema.append((raw - out[k], k))
	left = want - sum(out.values())
	for _frac, k in sorted(rema, reverse=True)[:max(0, left)]:
		out[k] += 1
	return out


def _survival(form="Tissue Culture", version=None):
	"""The share of what is ordered that is expected to reach the field.

	Per form: tissue culture and roots are hardened differently and lose
	differently, so grossing both orders up by one figure orders too few of
	whichever loses more. tc_supply owns the lookup so the two engines cannot
	read different provisions for the same material.
	"""
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	return max(0.01, ts.survival(form, version=version))


def _root_batches(weeks, ordered, per_root, root_weeks, lifts):
	"""When roots come out of the coldroom, and what each lift delivers.

	Planning asks one question of the roots: does what comes out of store, lifted
	on the protocol's dates, meet the demand? So the weeks the field wants plants
	are grouped by what ONE sticking can serve -- its first lift to its last --
	and that group comes out of store once, early enough for the first lift to
	land on the first week it serves.

	Capacity is not asked here. How many troughs that fills, whether the last one
	is part empty, which bench has room -- those are the propagation plan's, and
	deciding them at planning time made the plan refuse quantities the market had
	actually asked for.
	"""
	want = [(k, cint(weeks[k]["roots_needed"]), cint(weeks[k]["by_roots"]))
	        for k in ordered if cint(weeks[k].get("roots_needed"))]
	if not want:
		return []
	sched = sorted([L for L in (lifts or []) if cint(L.get("weeks_after")) >= 0],
	               key=lambda L: cint(L["weeks_after"]))
	if not sched:
		sched = [{"weeks_after": cint(root_weeks), "share_pct": 100.0}]
	first_at = cint(sched[0]["weeks_after"])
	span = cint(sched[-1]["weeks_after"]) - first_at

	batches, cur = [], None
	for land, roots, plants in want:
		if cur is not None and (getdate(land) - getdate(cur["first"])).days // 7 > span:
			batches.append(cur)
			cur = None
		if cur is None:
			cur = {"first": land, "weeks": [], "roots": 0, "plants": 0}
		cur["weeks"].append({"week": str(land), "week_iso": iso(land), "plants": plants})
		cur["roots"] += roots
		cur["plants"] += plants
	if cur:
		batches.append(cur)

	out = []
	for b in batches:
		stick = add_days(getdate(b["first"]), -7 * first_at)
		rows, used, carry = [], 0, 0
		for i, L in enumerate(sched):
			qty = (b["plants"] - used) if i == len(sched) - 1 else int(
				round(b["plants"] * flt(L.get("share_pct") or 0) / 100.0))
			used += qty
			on = add_days(stick, 7 * cint(L["weeks_after"]))
			nxt = (add_days(stick, 7 * cint(sched[i + 1]["weeks_after"]))
			       if i + 1 < len(sched) else None)
			covers = [w for w in b["weeks"]
			          if getdate(w["week"]) >= on and (not nxt or getdate(w["week"]) < nxt)]
			asked = sum(cint(w["plants"]) for w in covers)
			have = qty + carry
			carry = max(0, have - asked)
			rows.append({
				"lift_no": i + 1, "weeks_after": cint(L["weeks_after"]),
				"share_pct": flt(L.get("share_pct") or 0),
				"on": str(on), "on_iso": iso(on),
				"plants": qty, "covers": covers, "asked": asked,
				"in_hand": have, "surplus": carry,
				"short": max(0, asked - have),
			})
		out.append({
			"out_of_store": str(stick), "out_iso": iso(stick),
			"plant_roots": b["roots"], "roots": b["roots"],
			"cuttings": int(round(b["roots"] * flt(per_root))),
			"cuttings_per_root": flt(per_root),
			"plants": b["plants"],
			"serves_from": b["weeks"][0]["week"], "serves_to": b["weeks"][-1]["week"],
			"lifts": rows,
		})
	return out


def _root_order(pulls, lead_weeks):
	"""The single root order behind a season of withdrawals.

	Roots are lifted once, from a block that was grown for it, and they keep in
	a coldroom. Ordering them week by week the way tissue culture is ordered
	described a trade that does not exist -- fifty-six consignments of a few
	hundred roots each, a year apart from the weeks they serve.
	"""
	if not pulls:
		return []
	first = min(pulls)
	placed = add_days(getdate(first), -7 * cint(lead_weeks))
	return [{
		"week": str(placed), "week_iso": iso(placed),
		"roots": sum(pulls.values()),
		"arrives": str(first), "arrives_iso": iso(first),
		"drawn_from": str(first), "drawn_to": str(max(pulls)),
		"withdrawals": len(pulls),
	}]


def _demand_vs_production(plan):
	"""Demand against what the plan grows, week by week, as the plan has it.

	Read from the plan's own weekly rows rather than recomputed. A second
	opinion on a figure the plan already states is how two numbers for one
	thing get into a room.
	"""
	out, running = [], 0
	for w in plan.get("plan_weeks") or []:
		demand = cint(w.demand_stems)
		grown = cint(w.production_stems)
		running += grown - demand
		out.append({
			"year": cint(w.year), "week_no": cint(w.week_no),
			"week_iso": "%d-W%02d" % (cint(w.year), cint(w.week_no)),
			"week_start": str(w.week_start_date or ""),
			"demand": demand, "production": grown,
			"variance": grown - demand, "cumulative": running,
			"short": grown < demand,
		})
	return out


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


def _states_a_split(rows):
	"""Whether the route actually says how the crop divides, or says nothing."""
	return any(flt(r.get("share_pct") or 0) for r in rows
	           if (r.get("stage") or "") in ("TC", LIFT_STAGE))


def _tc_share(rows):
	"""The split the protocol already states, as a fraction."""
	tc = sum(flt(r.get("share_pct") or 0) for r in rows
	         if (r.get("stage") or "") == "TC")
	root = sum(flt(r.get("share_pct") or 0) for r in rows
	           if (r.get("stage") or "") == LIFT_STAGE)
	return (tc / (tc + root)) if (tc + root) else 1.0


def _ways_in(version):
	"""The stages this crop's material is bought at, biggest share first."""
	from upande_summer_flowers.summer_flowers import sourcing

	try:
		ways = sourcing.entry_ways(version) or []
	except Exception:
		return []
	ways = [w for w in ways if w.get("stage")]
	ways.sort(key=lambda w: -flt(w.get("share_pct") or 0))
	return [w["stage"] for w in ways]


def _saleable(v):
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import saleable

	return saleable(v)


def _loss_account(v, rows, stage):
	"""The losses between buying and a plant standing, and where each is stated.

	`stated` is the Grades & Losses tab -- the only place a person enters any of
	this. `steps` is what the route made of it, stage by stage, which is what
	actually sizes the order. They are shown together on purpose: a reader who
	has just been told the order is 1.79x the planting should be able to see the
	two percentages it came from without opening the protocol.
	"""
	from upande_summer_flowers.summer_flowers.crop_protocol import ROOTING_LOSSES

	root = flt(v.get("rooting_success_pct"))
	field = flt(v.get("field_establishment_pct"))
	rooting_applies = False
	establishment_applies = False
	steps = []
	survival, seen = 1.0, False
	for r in rows:
		st = r.get("stage") or ""
		if st == stage and not seen:
			seen = True
		if not seen:
			continue
		at_end = st == "Plants"
		loss = flt(r.get("loss_pct"))
		if loss:
			# Where this stage's figure came from. Listing every percentage on
			# the tab instead said "10% of cuttings rejected" on a seed route,
			# which has no cutting to reject -- a figure that is stated and not
			# applied reads as one that is, and the arithmetic then does not add
			# up for the reader checking it.
			if at_end:
				src = _("{0}% establish once planted").format(field)
				establishment_applies = True
			elif st in ROOTING_LOSSES:
				src = _("{0}% root").format(root)
				rooting_applies = True
			elif cint(r.get("is_purchase")):
				src = _("{0}% allowed on the order").format(
					flt(v.get("tc_order_loss_pct")))
			else:
				src = None
			steps.append({"stage": _("Planting out") if at_end
			              else (st or r.get("step")),
			              "loss_pct": round(loss, 4), "from": src})
		survival *= 1.0 - loss / 100.0
		if at_end:
			break
	return {
		"stated": {
			"rooting_success_pct": flt(v.get("rooting_success_pct")),
			"field_establishment_pct": flt(v.get("field_establishment_pct")),
			"cutting_reject_pct": flt(v.get("cutting_reject_pct")),
			"tc_order_loss_pct": flt(v.get("tc_order_loss_pct")),
			"cuttings_per_plant_required": flt(v.get("cuttings_per_plant_required")),
			# Not a loss of plants but of what they produce, so it is stated
			# apart from the chain above: it does not change what one seed
			# becomes, it changes how many plants the demand needs.
			# The grading loss, however the protocol spells it. Both fields are
			# the same kind of loss and a protocol stating both means both, so
			# the card reports what the yield was actually reduced by rather
			# than one of the two numbers behind it.
			"reject_pct": round((1.0 - _saleable(v)) * 100, 4),
			"stems_per_plant_life": flt(v.get("total_stems_per_plant_life")),
		},
		"steps": steps,
		"survival_pct": round(survival * 100, 2),
		# Which of the stated percentages bite on this way in. They are asked
		# separately because a route can carry one without the other: plants
		# bought ready are never rooted but still have to establish.
		"rooting_applies": rooting_applies,
		"establishment_applies": establishment_applies,
	}


def _units_for(plants, rows, stage):
	"""How many units of `stage` to buy to stand `plants` plants up.

	None where the route passes a pool. A motherstock is bought once and cut
	every week, so what one plantlet becomes is a season of cuttings and not a
	number of plants -- dividing by it here would size the order the way the
	procurement plan refuses to, many times too small. sourcing.requirement is
	the only thing that sizes a standing route, and it is not this card's job.
	"""
	if not plants:
		return 0
	seen = False
	for r in rows:
		st = r.get("stage") or ""
		if st == stage and not seen:
			seen = True
		if not seen:
			continue
		if st == "Plants":
			break
		if cint(r.get("is_standing")):
			return None
	per_unit = _plants_per_unit_from(rows, stage)
	return int(math.ceil(plants / per_unit)) if per_unit > 0 else None


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
def agree_material_plan(production_plan, tc_qty=None, roots_qty=None,
                        allocations=None):
	"""Write the chosen split down, then build what follows from it.

	The card could work the whole thing out and had nowhere to put it. A
	motherstock crop has Agree this line; a bought crop had nothing, so the
	figures lived on screen until the tab was closed.

	What it writes is the pair -- tissue culture and roots -- onto the
	production plan's committed fields, which already existed for the TC half.
	Then the two documents that follow: the Procurement Plan that orders it and
	the Propagation Plan that receives it. Each step is reported and each may
	fail on its own, because the decision is the thing that must survive.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	m = arrival_plan(plan, tc_qty=tc_qty, roots_qty=roots_qty)
	if not m:
		frappe.throw(_("There is nothing to agree: this plan has no new plantings."))

	asked = m.get("asked") or m.get("suggested") or {}
	tc = cint(asked.get("tc_qty") or asked.get("tc"))
	roots = cint(asked.get("roots_qty") or asked.get("roots"))
	first = (m.get("tc_order_schedule") or [{}])[0].get("week")

	fields = {
		"tc_choice_committed": 1,
		"tc_plants_committed": tc,
		"roots_committed": roots,
		"tc_order_date_committed": first,
	}
	if allocations not in (None, ""):
		# Kept as written rather than normalised: who sends how much on what
		# cycle is an agreement with a supplier, and rounding it to fit a model
		# would quietly change what was agreed.
		fields["tc_supplier_allocations"] = (
			allocations if isinstance(allocations, str)
			else frappe.as_json(allocations))
	plan.db_set(fields, update_modified=False)
	frappe.clear_document_cache("Summer Flower Production Plan", plan.name)
	frappe.db.commit()

	out = {"production_plan": plan.name, "tc": tc, "roots": roots,
	       "order_by": first, "steps": []}
	for label, fn in _after_material_agreement(plan.name, tc):
		try:
			out["steps"].append({"step": label, "ok": True, "result": fn()})
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(),
			                 "Material plan for %s: %s" % (plan.name, label))
			out["steps"].append({"step": label, "ok": False,
			                     "error": frappe.utils.strip_html(str(e))[:200]})
	return out


def _after_material_agreement(production_plan, tc):
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import build
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_propagation_plan.summer_flower_propagation_plan import (
			build_from_plan,
		)

	def procurement():
		# Both levers, because which one bites depends on the route and the card
		# does not need to know. tc_qty sizes a pool; units_qty trims an order
		# bought one unit per plant -- and passing only the first is why a crop
		# bought as plants was agreed at 4,000 and its procurement plan went on
		# saying 6,000.
		name = build(production_plan, replace=1,
		             tc_qty=tc or None, units_qty=tc or None)
		apply_held_choices(production_plan)
		return {"name": name}

	def propagation():
		r = build_from_plan(production_plan, as_dict=True)
		return r if isinstance(r, dict) else {"name": r}

	# Agreeing a quantity is a decision about the PLAN. It used to build the
	# propagation plan here as well, which commits the bench to raising a figure
	# nobody has agreed to buy yet -- the supplier has not been asked, and what
	# is raised has to be what arrives. The propagation plan is built where that
	# is known: when the schedule is confirmed on the Procurement tab.
	return [(_("Procurement Plan"), procurement)]


@frappe.whitelist()
def propagation_units(farm=None):
	"""The benches that could raise this plan's material."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	f = {"custom_sf_is_propagation_unit": 1}
	rows = frappe.get_all("Warehouse", filters=f, order_by="name",
	                      fields=["name", "custom_farm as farm",
	                              "custom_sf_capacity_plants as capacity"])
	# The farm's own benches first: a unit on the farm the crop is grown at is
	# nearly always the answer, and on a site with several it is the one that
	# needs no scrolling.
	rows.sort(key=lambda r: (0 if farm and r.farm == farm else 1, r.name))
	return rows


def _raised_by(plan):
	"""The raising allocation as it stands, from the plan or from what is held."""
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": plan.name, "docstatus": ["<", 2]},
	                        "name", order_by="creation desc")
	if q:
		doc = frappe.get_doc("Summer Flower Procurement Plan", q)
		return [{"raises": r.raises, "type": r.alloc_type, "party": r.party,
		         "share_pct": flt(r.share_pct), "plants": cint(r.plants),
		         "lead_weeks": cint(r.lead_weeks), "start_by": str(r.start_by or ""),
		         "plan": r.propagation_plan, "pending": 0}
		        for r in doc.propagation_allocation]
	return [{"raises": r.get("raises"),
	         "type": "Outside Propagator" if r.get("supplier") else "Own Unit",
	         "party": r.get("supplier") or r.get("unit"),
	         "share_pct": flt(r.get("share_pct")), "plants": 0,
	         "lead_weeks": 0, "start_by": "", "plan": None, "pending": 1}
	        for r in _held(plan, "sf_pending_raising")]


def _held(plan, field):
	"""What was chosen on the card before there was a plan to write it on."""
	raw = plan.get(field)
	if not raw:
		return []
	try:
		rows = frappe.parse_json(raw)
	except Exception:
		return []
	return rows if isinstance(rows, list) else []


def _hold(production_plan, field, rows):
	rows = frappe.parse_json(rows) if isinstance(rows, str) else (rows or [])
	frappe.db.set_value("Summer Flower Production Plan", production_plan, field,
	                    frappe.as_json(rows), update_modified=False)
	frappe.clear_document_cache("Summer Flower Production Plan", production_plan)


def apply_held_choices(production_plan):
	"""Carry the card's choices onto the procurement plan that now exists.

	Choosing a seller or a bench is a decision about the season, not about a
	document: a planner makes it while looking at the demand, which is before
	the plan is saved and the procurement plan written. So the choice is held
	and applied here, and the card can change it afterwards as usual.
	"""
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	sell, raise_ = _held(plan, "sf_pending_selling"), _held(plan, "sf_pending_raising")
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": production_plan, "docstatus": 0}, "name")
	if q and (sell or raise_):
		# Both tables in ONE save. Writing the sellers first left the plan with
		# nobody raising it for the length of that save, and the guard that
		# refuses exactly that threw -- taking the procurement plan with it.
		doc = frappe.get_doc("Summer Flower Procurement Plan", q)
		if sell:
			doc.set("purchase_allocation", [])
			for r in sell:
				doc.append("purchase_allocation", {
					"entry_stage": r.get("entry_stage"),
					"supplier": r.get("supplier") or None,
					"share_pct": flt(r.get("share_pct")),
					"rate": flt(r.get("rate")) or 0,
					"lead_weeks": cint(r.get("lead_weeks")) or 0})
		if raise_:
			doc.set("propagation_allocation", [])
			for r in raise_:
				if not (r.get("unit") or r.get("supplier")):
					continue
				doc.append("propagation_allocation", {
					"alloc_type": ("Outside Propagator" if r.get("supplier")
					               else "Own Unit"),
					"raises": r.get("raises"),
					"propagation_unit": r.get("unit") or None,
					"supplier": r.get("supplier") or None,
					"share_pct": flt(r.get("share_pct")),
					"rate_per_plant": flt(r.get("rate")) or 0})
		# The planner is still mid-flight: this is the same write that built the
		# plan, not a person saving a half-filled one.
		doc.flags.rebuilding = True
		doc.flags.ignore_permissions = True
		doc.save()
	if sell or raise_:
		frappe.db.set_value("Summer Flower Production Plan", production_plan,
		                    {"sf_pending_selling": None, "sf_pending_raising": None},
		                    update_modified=False)
		frappe.clear_document_cache("Summer Flower Production Plan", production_plan)
	return {"selling": len(sell), "raising": len(raise_)}


@frappe.whitelist()
def selling_choices(production_plan):
	"""Who sells each way in, and what is already chosen, for the card.

	The companion to raising_choices. The card had a supplier allocator of its
	own, writing quantities onto the production plan, while the orders were
	raised off a table on the procurement plan -- two places to answer one
	question, and only one of them was read.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": production_plan, "docstatus": 0},
	                        "name")
	if not q:
		# Nothing bought yet, but the choice is still worth making: the card can
		# say who sells it before the plan is saved, and the choice is held until
		# there is a procurement plan to carry it.
		plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
		return {"procurement_plan": None, "pending": 1,
		        "ways": _ways_in(frappe.get_cached_doc(
			        "Crop Protocol Version", plan.protocol)) if plan.protocol else [],
		        "units_by_way": {},
		        "chosen": _held(plan, "sf_pending_selling")}
	doc = frappe.get_doc("Summer Flower Procurement Plan", q)
	return {
		"procurement_plan": q,
		"ways": list(doc.units_by_stage()) or doc.ways_in(),
		"units_by_way": {k: cint(v) for k, v in doc.units_by_stage().items()},
		"lead_weeks": cint(doc.lead_weeks),
		"chosen": [{"entry_stage": r.entry_stage, "supplier": r.supplier,
		            "share_pct": flt(r.share_pct), "units": cint(r.units),
		            "rate": flt(r.rate), "amount": flt(r.amount),
		            "lead_weeks": cint(r.lead_weeks),
		            "order_by": str(r.order_by or ""),
		            "arrives": str(r.arrives or "")}
		           for r in doc.purchase_allocation],
	}


@frappe.whitelist()
def set_selling(production_plan, rows):
	"""Replace who sells this plan's material with what the card shows."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	rows = frappe.parse_json(rows) if isinstance(rows, str) else (rows or [])
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": production_plan, "docstatus": 0},
	                        "name")
	if not q:
		_hold(production_plan, "sf_pending_selling", rows)
		return selling_choices(production_plan)
	doc = frappe.get_doc("Summer Flower Procurement Plan", q)
	doc.set("purchase_allocation", [])
	for r in rows:
		doc.append("purchase_allocation", {
			"entry_stage": r.get("entry_stage"),
			"supplier": r.get("supplier") or None,
			"share_pct": flt(r.get("share_pct")),
			"rate": flt(r.get("rate")) or 0,
			"lead_weeks": cint(r.get("lead_weeks")) or 0})
	doc.flags.ignore_permissions = True
	doc.save()
	return selling_choices(production_plan)


@frappe.whitelist()
def raising_choices(production_plan):
	"""Everything the "who raises it" chooser needs, in one call.

	What is already chosen comes back with it. A chooser that opens blank over
	an allocation that exists invites somebody to make a second one, and the
	first they ever hear of the first is the share totalling 200%.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": plan.name, "docstatus": 0}, "name")
	doc = frappe.get_doc("Summer Flower Procurement Plan", q) if q else None

	units = []
	for w in frappe.get_all(
			"Warehouse", filters={"custom_sf_is_propagation_unit": 1},
			order_by="name",
			fields=["name", "custom_farm as farm",
			        "custom_sf_capacity_plants as capacity",
			        "custom_sf_weekly_intake_plants as weekly"]):
		# What this bench is already carrying for everyone else. A capacity with
		# nothing taken off it is the number that gets over-allocated.
		taken = frappe.db.sql("""
			select ifnull(sum(a.plants), 0)
			from `tabSummer Flower Propagation Allocation` a
			join `tabSummer Flower Procurement Plan` p on p.name = a.parent
			where a.propagation_unit = %s and p.docstatus < 2
			  and p.name != %s""", (w.name, q or ""))[0][0]
		w["committed"] = cint(taken)
		w["free"] = max(0, cint(w.capacity) - cint(taken))
		w["here"] = 1 if (plan.farm and w.farm == plan.farm) else 0
		units.append(w)
	units.sort(key=lambda u: (-u["here"], -u["free"], u["name"]))

	propagators = frappe.get_all(
		"Supplier", filters={"custom_sf_is_propagator": 1, "disabled": 0},
		fields=["name", "custom_sf_propagation_weeks as lead_weeks",
		        "custom_sf_rate_per_plant as rate"], order_by="name")

	chosen, ways, plants = [], [], {}
	if not doc:
		# Held on the production plan until there is a procurement plan.
		chosen = _held(plan, "sf_pending_raising")
		if plan.protocol:
			ways = _ways_in(frappe.get_cached_doc("Crop Protocol Version", plan.protocol))
			plants = {w: 0 for w in ways}
	if doc:
		plants = doc.plants_by_stage()
		ways = doc.ways_in()
		for r in doc.propagation_allocation:
			chosen.append({"idx": r.idx, "type": r.alloc_type, "raises": r.raises,
			               "unit": r.propagation_unit, "supplier": r.supplier,
			               "party": r.party, "share_pct": flt(r.share_pct),
			               "plants": cint(r.plants), "rate": flt(r.rate_per_plant),
			               "lead_weeks": cint(r.lead_weeks),
			               "start_by": str(r.start_by or ""),
			               "plan": r.propagation_plan})
	return {"procurement_plan": q, "units": units, "propagators": propagators,
	        "chosen": chosen, "ways": ways,
	        "plants_by_way": {k: cint(v) for k, v in plants.items()}}


@frappe.whitelist()
def set_raising(production_plan, rows):
	"""Replace who raises this plan's material with what the chooser shows."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	rows = frappe.parse_json(rows) if isinstance(rows, str) else (rows or [])
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": production_plan, "docstatus": 0},
	                        "name")
	if not q:
		_hold(production_plan, "sf_pending_raising", rows)
		return raising_choices(production_plan)
	doc = frappe.get_doc("Summer Flower Procurement Plan", q)
	doc.set("propagation_allocation", [])
	for r in rows:
		if not (r.get("unit") or r.get("supplier")):
			continue
		doc.append("propagation_allocation", {
			"alloc_type": ("Outside Propagator" if r.get("supplier") else "Own Unit"),
			"raises": r.get("raises"),
			"propagation_unit": r.get("unit") or None,
			"supplier": r.get("supplier") or None,
			"share_pct": flt(r.get("share_pct")),
			"rate_per_plant": flt(r.get("rate")) or 0})
	doc.flags.ignore_permissions = True
	doc.save()
	return raising_choices(production_plan)


@frappe.whitelist()
def set_propagation_unit(production_plan, unit=None, supplier=None, share_pct=100):
	"""Say who raises this plan's material, from the dashboard.

	The card could decide the quantity and who sells it and then had nowhere to
	say who raises what arrives -- that answer lived only on the procurement
	plan, which is a document away from the person making the decision.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	if not (unit or supplier):
		frappe.throw(_("Name a unit or a propagator."))
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": production_plan, "docstatus": 0},
	                        "name")
	if not q:
		frappe.throw(_("There is no draft procurement plan to allocate on. Save "
		               "the material plan first."))
	doc = frappe.get_doc("Summer Flower Procurement Plan", q)
	stage = doc.main_entry_stage()
	rows = [r for r in (doc.get("propagation_allocation") or [])
	        if (r.raises or stage) == stage]
	for r in rows:
		doc.remove(r)
	doc.append("propagation_allocation", {
		"alloc_type": "Outside Propagator" if supplier else "Own Unit",
		"raises": stage, "propagation_unit": unit, "supplier": supplier,
		"share_pct": flt(share_pct)})
	doc.flags.ignore_permissions = True
	doc.save()
	doc.reload()
	r = doc.propagation_allocation[-1]
	return {"procurement_plan": doc.name, "raises": r.raises,
	        "party": r.party, "share_pct": flt(r.share_pct),
	        "plants": cint(r.plants), "lead_weeks": cint(r.lead_weeks),
	        "start_by": str(r.start_by or ""), "basis": r.ready_basis}


@frappe.whitelist()
def propagation_overview(production_plan):
	"""The propagation plan as dates and quantities, and nothing else.

	Six questions, whichever way the crop is raised: when the tissue culture is
	ordered, when it lands, when roots are ordered and land, when the block is
	multiplied, and how many plants reach the farm in which week. Both engines
	already know all of it; this puts them behind one shape so the screen does
	not have to ask which kind of crop it is looking at.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	ms = frappe.db.get_value(
		"Summer Flower Motherstock Plan", {"production_plan": plan.name}, "name")
	prop = frappe.db.get_value(
		"Summer Flower Propagation Plan",
		{"variety": plan.variety,
		 "season_start_year": season_year(plan.season_start_year),
		 "status": ["!=", "Rejected"]}, "name")

	out = {
		"plan": plan.name, "variety": plan.variety, "farm": plan.farm,
		"propagation_plan": prop,
		"kind": "motherstock" if ms else "bought",
		"tc": None, "roots": None, "multiply": [], "farm_weeks": [],
		"plants_total": 0, "first_planting": None, "last_planting": None,
	}
	out.update(_ms_overview(ms) if ms else _bought_overview(plan))

	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		d = _monday(b.planting_date)
		out["plants_total"] += cint(b.plants)
		if not out["first_planting"] or d < getdate(out["first_planting"]):
			out["first_planting"] = str(d)
		if not out["last_planting"] or d > getdate(out["last_planting"]):
			out["last_planting"] = str(d)
	out["first_planting_iso"] = iso(out["first_planting"])
	out["last_planting_iso"] = iso(out["last_planting"])
	return out


def _ms_overview(ms_name):
	"""Dates and quantities off an agreed motherstock line."""
	d = frappe.get_doc("Summer Flower Motherstock Plan", ms_name)
	mult = []
	for g in d.get("generation_table") or []:
		if cint(g.generation) <= 1:
			continue
		mult.append({
			"n": cint(g.generation) - 1,
			"sent": str(g.get("stuck_date") or ""),
			"sent_iso": iso(g.get("stuck_date")),
			"lands": str(g.first_cut_date or ""),
			"lands_iso": iso(g.first_cut_date),
			"plants": cint(g.mothers),
		})
	farm_weeks = [{
		"week": str(w.week_start), "week_iso": iso(w.week_start),
		"to_farm": cint(w.to_field), "needed": cint(w.demand),
		"short": cint(w.shortfall),
	} for w in (d.get("schedule") or []) if cint(w.to_field) or cint(w.demand)]
	return {
		"motherstock_plan": d.name,
		"tc": {"qty": cint(d.tc_to_order), "order": str(d.order_by_date or ""),
		       "order_iso": iso(d.order_by_date),
		       "arrive": str(d.tc_arrival_date or ""),
		       "arrive_iso": iso(d.tc_arrival_date)},
		"first_cut": str(d.first_cut_date or ""),
		"first_cut_iso": iso(d.first_cut_date),
		"block_cleared": str(d.line_end_date or ""),
		"block_cleared_iso": iso(d.line_end_date),
		"multiply": mult,
		"farm_weeks": farm_weeks,
		"weeks_met": cint(d.weeks_met), "weeks_required": cint(d.weeks_required),
	}


def _bought_overview(plan):
	"""Dates and quantities for a crop that is bought, not cut from a pool."""
	m = arrival_plan(plan)
	if not m:
		return {}
	tc_first = (m.get("tc_order_schedule") or [{}])[0]
	root_first = (m.get("root_order_schedule") or [{}])[0]
	tc_qty = sum(r["tc"] for r in (m.get("tc_order_schedule") or []))
	root_qty = sum(r["roots"] for r in (m.get("root_order_schedule") or []))
	return {
		"tc": {"qty": cint(tc_qty), "order": tc_first.get("week") or "",
		       "order_iso": tc_first.get("week_iso"),
		       "arrive": str(add_days(getdate(tc_first["week"]),
		                              7 * cint(m["tc_lead_weeks"])))
		       if tc_first.get("week") else "",
		       "arrive_iso": iso(add_days(getdate(tc_first["week"]),
		                                  7 * cint(m["tc_lead_weeks"])))
		       if tc_first.get("week") else None},
		"roots": {"qty": cint(root_qty), "order": root_first.get("week") or "",
		          "order_iso": root_first.get("week_iso"),
		          "per_plant": flt(m.get("returns_per_plant")),
		          "arrive": str(add_days(getdate(root_first["week"]),
		                                 7 * cint(m["root_lead_weeks"])))
		          if root_first.get("week") else "",
		          "arrive_iso": iso(add_days(getdate(root_first["week"]),
		                                     7 * cint(m["root_lead_weeks"])))
		          if root_first.get("week") else None} if root_qty else None,
		"farm_weeks": [{
			"week": w["week"], "week_iso": w["week_iso"],
			"to_farm": cint(w["plants"]), "from_tc": cint(w["by_tc"]),
			"from_roots": cint(w["by_roots"]), "needed": cint(w["plants"]),
			"short": 0,
		} for w in (m.get("weeks") or [])],
		"blocked": m.get("blocked"),
	}


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
