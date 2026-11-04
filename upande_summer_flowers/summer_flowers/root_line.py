# Copyright (c) 2026, James Kiruga and contributors
"""The other way to raise a crop: grow a block, lift it once, plant the roots."""

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
	"""What to buy, when to plant the block, when to lift it, and what is left over."""
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
	"""Roots already in store, as real stock rather than a note on a plan."""
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
	"""The ways this crop can be got, costed against each other."""
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
			out *= 1.0 - flt(r.get("loss_pct") or 0) / 100.0
			break
		lift = flt(r.get("returns_per_plant") or 0)
		out *= lift if lift > 0 else flt(r.get("yields_per_unit") or 1)
		out *= 1.0 - flt(r.get("loss_pct") or 0) / 100.0
	return out


def arrival_plan(plan, tc_share=None, returns_per_plant=None,
                 tc_qty=None, roots_qty=None):
	"""Both ways in, landing plants in the week the field wants them."""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	rows = cp.route_rows(v) or []
	per_root = flt(returns_per_plant if returns_per_plant not in (None, "")
	               else _lift_return(rows) or 0)

	wanted = sum(cint(b.plants) for b in plan.plan_blocks
	             if cint(b.is_new_planting))
	# Quantities beat a percentage, because a percentage is not what anybody
	if not (cint(tc_qty) or cint(roots_qty)) and tc_share in (None, "") \
			and cint(plan.get("tc_choice_committed")) \
			and not _agreement_stale(plan, wanted, per_root):
		tc_qty = cint(plan.get("tc_plants_committed"))
		roots_qty = cint(plan.get("roots_committed"))

	# What is expected to reach the field out of what is ordered. A provision,
	keep = _survival("Tissue Culture", plan.get("protocol"))
	keep_roots = _survival("Roots", plan.get("protocol"))

	asked = {}
	# Where the split came from, because the card has been calling all three
	ways = _ways_in(v)
	one_way = len(ways) < 2

	# What the ROUTE does to a bought unit on its way to the ground, which is a
	root_ok = flt(v.rooting_success_pct)
	field_ok = flt(v.field_establishment_pct)
	if root_ok and field_ok:
		route_keep = (root_ok / 100.0) * (field_ok / 100.0)
	else:
		per_plant = flt(v.cuttings_per_plant_required) or 1.0
		route_keep = (1.0 / per_plant) if per_plant > 0 else 1.0
	route_keep = max(0.0, min(1.0, route_keep)) or 1.0

	origin = "protocol"
	if cint(tc_qty) or cint(roots_qty):
		from_tc = int(round(cint(tc_qty) * keep * route_keep))
		from_roots = (int(cint(roots_qty) * per_root * keep_roots)
		              if per_root > 0 else 0)
		covered = from_tc + from_roots
		share = (1.0 if one_way
		         else max(0.0, min(1.0, (float(from_tc) / wanted) if wanted else 1.0)))
		asked = {
			# The QUANTITY asked for, not what it is worth. These were the same
			"tc_qty": cint(tc_qty), "roots_qty": cint(roots_qty),
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
	primary = (ways[0] if ways else
	           next((r.stage for r in rows if r.get("stage")), "TC"))
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
	share_by_week = _apportion(
		weeks, share, cint(asked["plants_from_tc"]) if asked else None)

	# Where our own unit raises it, the roots are chosen by the trough and the
	troughs = None
	if not one_way:
		troughs = _by_the_trough(plan, v, weeks, sorted(weeks), share_by_week,
		                         per_root)
		if troughs:
			for land in weeks:
				got = cint(troughs["delivered"].get(land, 0))
				share_by_week[land] = max(0, cint(weeks[land]["plants"]) - got)

	# What one bought unit of the first way becomes, and what survives the first
	per_unit_one = _plants_per_unit_from(rows, primary)
	account = _loss_account(v, rows, primary)
	# Germination is the first of the two percentages the protocol states, and
	_spp = flt((account.get("stated") or {}).get("stems_per_plant_life"))
	_reject = flt((account.get("stated") or {}).get("reject_pct")) / 100.0
	first_pct = (flt(account["stated"]["rooting_success_pct"])
	             if account.get("rooting_applies") else 0.0)
	# And the second: what fails to establish once it is planted out. Named
	second_pct = (flt(account["stated"]["field_establishment_pct"])
	              if account.get("establishment_applies") else 0.0)
	for land, w in weeks.items():
		by_tc = share_by_week[land]
		# On a one-way crop the rest of the week is not a second way in, it is
		by_root = 0 if one_way else w["plants"] - by_tc
		# Roots come out of store by the trough, so the week's root count is
		roots = (int(math.ceil(by_root / (per_root * keep_roots)))
		         if (per_root > 0 and keep_roots > 0 and by_root) else None)
		# The week's own order, and what is left of it after each loss. The
		establish = (second_pct / 100.0) if second_pct else 1.0
		week_plants = cint(w["plants"])
		planting = (_plantable(v, week_plants / establish)
		            if (week_plants and establish > 0) else 0)
		to_plant = (int(round(planting * by_tc / float(week_plants)))
		            if (week_plants and by_tc) else 0)
		# What one bought unit puts IN THE GROUND: what it finally becomes, less
		per_unit_at_plant = ((per_unit_one / establish)
		                     if establish > 0 else per_unit_one)
		units_one = (int(math.ceil(to_plant / per_unit_at_plant - 1e-9))
		             if (to_plant and per_unit_at_plant > 0) else 0)
		# The week as a ladder down from what is bought to what is left
		_cut = int(round(cint(w["plants"]) * _spp))
		_sale = int(round(_cut * (1.0 - _reject)))
		w.update({"stems_cut": _cut, "stems_saleable": _sale,
		          "stems_lost_grading": max(0, _cut - _sale)})
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
		w["sow_week"] = str(add_days(land, -7 * tc_weeks))
		w["sow_iso"] = iso(w["sow_week"])
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
	# What the card says to BUY, worked out once so the boxes, the tiles and the
	_units_total = (sum(cint(weeks[k].get("units_tc")) for k in ordered)
	                if _units_for(1, rows, primary) is not None else None)
	_roots_total = (cint(troughs["roots_total"]) if troughs
	                else sum(cint(weeks[k]["roots_needed"]) for k in ordered))
	# A share of the plan sent down a way that cannot say what a unit becomes is
	blocked = None
	if not one_way and share < 1.0 and per_root <= 0:
		pct = round((1 - share) * 100, 1)
		# "Set the field" is the wrong instruction once the field IS set and the
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
	ways = _ways_in(v)
	return {
		"blocked": blocked,
		"asked": asked or None,
		"ways": len(ways),
		"route_summary": v.get("route_summary"),
		# Who raises each way in, beside who sells it. Choosing a lab answers
		"raised_by": _raised_by(plan),
		# The same answer the losses and the ladder were worked out from, rather
		"primary_stage": primary,
		"secondary_stage": ways[1] if len(ways) > 1 else None,
		# What was agreed, if anything was. A saved plan that opens as a blank
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
		"tc_units_total": _units_total,
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
		# The harvest end of the same chain, so the week table can show why the
		"stems_cut_total": sum(cint(weeks[k].get("stems_cut")) for k in ordered),
		"stems_saleable_total": sum(cint(weeks[k].get("stems_saleable"))
		                            for k in ordered),
		"stems_lost_grading_total": sum(cint(weeks[k].get("stems_lost_grading"))
		                                for k in ordered),
		"grading_reject_pct": round(_reject * 100, 2),
		"stems_per_plant_life": _spp,
		# The same figures as one chain, because the tiles above answer "how
		"ladder": _ladder(
			account,
			sum(weeks[k]["by_tc"] for k in ordered),
			sum(cint(weeks[k].get("to_plant_tc")) for k in ordered),
			(sum(cint(weeks[k].get("units_tc")) for k in ordered)
			 if _units_for(1, rows, primary) is not None else None),
			primary, first_pct, second_pct, per_unit_one),
		# How the order got bigger than the planting. The card could already be
		"losses": account,
		# A route that takes its material off the farm's own crop can only do
		"own_stock": _own_stock(plan, v, rows, primary,
		                        sum(cint(weeks[k].get("units_tc")) for k in ordered)),
		# What is actually bought. Summing the weeks counts what each week could
		"roots_total": _roots_total,
		"weeks": [dict(week=str(k), **weeks[k]) for k in ordered],
		# "tc" not "plantlets": the farm buys tissue culture, and calling it
		"tc_order_schedule": [{"week": k, "week_iso": iso(k), "tc": v}
		                      for k, v in sorted(tc_orders.items())],
		# One line, not fifty. The whole season's roots are bought in one
		"root_order_schedule": (
			[{"week": _back(troughs["settings"][0]["set_week"], root_lead),
			  "week_iso": iso(_back(troughs["settings"][0]["set_week"], root_lead)),
			  "roots": cint(troughs["roots_total"])}]
			if troughs else _root_order(root_pulls, root_lead)),
		# Out of store in trough-fulls, lifted out to the weeks that want them.
		"root_batches": [] if troughs else _root_batches(
			weeks, ordered, per_root, root_weeks,
			# The ROOTS lifts. A protocol carries a lift table per form, and
			[{"weeks_after": cint(L.weeks_after_arrival), "share_pct": flt(L.share_pct)}
			 for L in (v.get("lifts") or [])
			 if (L.form or "").strip().lower().startswith("root")]),

		# And what comes back out of the coldroom, week by week.
		"root_withdrawals": [{"week": k, "week_iso": iso(k), "roots": v}
		                     for k, v in sorted(root_pulls.items())],
		"roots_stored": sum(root_pulls.values()),
		# The troughs themselves, where our own unit is the one setting them:
		"troughs": ({"unit": troughs["unit"],
		             "owned": troughs["troughs_owned"],
		             "used": troughs["troughs_used"],
		             "roots": troughs["roots_total"],
		             "plants": troughs["plants_total"],
		             "share_wanted": troughs["share_wanted"],
		             # Weeks as text, like every other week on this card: the
		             "settings": [{"set_week": str(t["set_week"]),
		                           "set_week_iso": iso(t["set_week"]),
		                           "capacity_plants": t["capacity_plants"],
		                           "roots": t["roots"],
		                           "deliveries": [{"week": str(d["week"]),
		                                           "week_iso": iso(d["week"]),
		                                           "plants": d["plants"]}
		                                          for d in t["deliveries"]]}
		                          for t in troughs["settings"]]}
		            if troughs else None),
		# What the propagation unit is being asked to hand over, and when. This
		"propagation_request": [
			{"week": str(k), "week_iso": iso(k), "plants": weeks[k]["plants"],
			 "from_tc": weeks[k]["by_tc"], "from_roots": weeks[k]["by_roots"]}
			for k in ordered if weeks[k]["plants"]
		],
		# What the boxes should open on. An empty box asks somebody to invent a
		"suggested": {
			# Grossed up by the loss provision: what has to be ORDERED for that
			"tc": int(math.ceil((_units_total if _units_total is not None
			                     else sum(weeks[k]["by_tc"] for k in ordered)) / keep)),
			# Already grossed up: a week's roots are by_root / (per_root x
			"roots": _roots_total,
			# Named from the route. "100% tissue culture" over a crop lifted as
			"from": _("{0}, {1}% {2}").format(
				SPLIT_FROM.get(origin, SPLIT_FROM["protocol"]),
				round(share * 100, 1), primary.lower())
			+ (_(", grossed up for losses of {0}% on {1} and {2}% on {3}")
			   .format(round((1 - keep) * 100, 1), primary.lower(),
			           round((1 - keep_roots) * 100, 1),
			           (secondary or _("the second way")).lower())
			   if (keep < 1 or keep_roots < 1) else ""),
		},
		"split_from": origin,
		"survival_pct": round(keep * 100, 2),
		"roots_survival_pct": round(keep_roots * 100, 2),
		"roots_in_store": root_stock(plan),
		# What the whole exercise is for. Ordering material is a means; the
		"demand_vs_production": _demand_vs_production(plan),
		# The tolerance the planting programme was solved against, so the table
		"variance_band_pct": flt(plan.get("weekly_variance_band_pct")) or 10.0,
		"weeks_outside_band": cint(plan.get("weeks_outside_band")),
	}


def _agreed_plants(plan, per_root):
	"""Plants the committed pair covers: the tissue culture plus what roots give."""
	return cint(plan.get("tc_plants_committed")) + int(
		cint(plan.get("roots_committed")) * flt(per_root))


def _agreement_stale(plan, wanted, per_root):
	"""Has the plan moved away from what was agreed as its material?"""
	agreed = _agreed_plants(plan, per_root)
	if not agreed or not wanted:
		return False
	return abs(agreed - wanted) > max(1, wanted * 0.01)


def _committed(plan):
	"""The agreement already written to this plan, or nothing."""
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
	"""Tissue culture per week: proportional, and adding to the total exactly."""
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
	"""The share of what is ordered that is expected to reach the field."""
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	return max(0.01, ts.survival(form, version=version))


def _back(week, weeks_before):
	"""The Monday `weeks_before` weeks before this one."""
	return _monday(add_days(getdate(week), -7 * cint(weeks_before)))


def _internal_unit(plan):
	"""Our own propagation unit for this plan, if that is who raises it."""
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": plan.name, "docstatus": ["<", 2]},
	                        "name")
	if q:
		for r in frappe.get_all("Summer Flower Propagation Allocation",
		                        filters={"parent": q}, fields=["alloc_type",
		                                                       "propagation_unit"]):
			if r.propagation_unit and r.alloc_type != "Outside Propagator":
				return r.propagation_unit
	# The held rows are the chooser's own, which name the unit "unit"; the
	for r in _held(plan, "sf_pending_raising"):
		if r.get("supplier"):
			continue
		unit = r.get("unit") or r.get("propagation_unit")
		if unit:
			return unit
	return None


def _trough_capacities(unit):
	"""Every trough standing in the unit, in plants. Empty where the"""
	if not unit or not frappe.db.table_exists("Propagation Trough"):
		return []
	return [cint(t.plants_per_trough) for t in frappe.get_all(
		"Propagation Trough",
		filters={"propagation_unit": unit, "is_active": 1,
		         "status": ["!=", "Out of Service"]},
		fields=["plants_per_trough"]) if cint(t.plants_per_trough) > 0]


def _root_lifts(v):
	"""The root lift table: when a trough pays back, and how much of itself."""
	return [{"weeks_after": cint(L.weeks_after_arrival),
	         "share_pct": flt(L.share_pct)}
	        for L in (v.get("lifts") or [])
	        if (L.form or "").strip().lower().startswith("root")]


def _by_the_trough(plan, v, weeks, ordered, share_by_week, per_root):
	"""Re-cut the root share into whole troughs, where our own unit raises it."""
	from upande_summer_flowers.summer_flowers import trough_plan

	unit = _internal_unit(plan)
	caps = _trough_capacities(unit)
	lifts = _root_lifts(v)
	if not caps or not lifts or not (per_root > 0):
		return None
	wanted = {k: max(0, cint(weeks[k]["plants"]) - cint(share_by_week.get(k, 0)))
	          for k in ordered}
	budget = sum(wanted.values())
	if budget <= 0:
		return None
	out = trough_plan.plan_troughs(wanted, ordered, lifts, caps, per_root,
	                               budget_plants=budget)
	if not out["settings"]:
		return None
	out["unit"] = unit
	out["troughs_owned"] = len(caps)
	out["share_wanted"] = budget
	return out


def _root_batches(weeks, ordered, per_root, root_weeks, lifts):
	"""When roots come out of the coldroom, and what each lift delivers."""
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
	"""The single root order behind a season of withdrawals."""
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
	"""Demand against what the plan grows, week by week, as the plan has it."""
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
	"""2027-W06. The farm plans in weeks and reads dates back into them, so the"""
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


def _own_stock(plan, v, rows, primary, units_needed):
	"""What the farm has standing to cut this plan's material from, and the gap."""
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.crop_protocol import ROUTE_ENTRY
	import upande_summer_flowers.summer_flowers.stages as _st

	if not primary or _st.kind(primary) != _st.TAKEN:
		return None
	dates = [b.planting_date for b in plan.plan_blocks
	         if cint(b.is_new_planting) and b.planting_date]
	if not dates:
		return None
	# Excluding this plan's own plantings: a crop cannot be cut from itself.
	mine = {b.existing_planting for b in plan.plan_blocks if b.existing_planting}
	out = sourcing.own_stock(v, plan.farm, plan.variety, min(dates), exclude=mine)
	out["units_needed"] = cint(units_needed)
	out["short_by"] = max(0, cint(units_needed) - cint(out["units_available"]))
	return out


def _loss_account(v, rows, stage):
	"""The losses between buying and a plant standing, and where each is stated."""
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
			"reject_pct": round((1.0 - _saleable(v)) * 100, 4),
			"stems_per_plant_life": flt(v.get("total_stems_per_plant_life")),
		},
		"steps": steps,
		"survival_pct": round(survival * 100, 2),
		# Which of the stated percentages bite on this way in. They are asked
		"rooting_applies": rooting_applies,
		"establishment_applies": establishment_applies,
	}


def _plantable(v, plants):
	"""Round a plant count up to something that can actually be planted."""
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import size_planting

	plants = int(math.ceil(flt(plants) - 1e-9))
	if plants <= 0:
		return 0
	try:
		sized = cint(size_planting(v, plants)[1])
	except Exception:
		return plants
	return sized or plants


def _ladder(account, plants, to_plant, units, stage, rooting_pct, field_pct,
            per_unit=1.0):
	"""The order as two tables, because the losses sit either side of a handover."""
	st = account.get("stated") or {}
	spp = flt(st.get("stems_per_plant_life"))
	reject = flt(st.get("reject_pct"))
	saleable = spp * (1.0 - reject / 100.0)
	if not cint(plants) or saleable <= 0:
		return None

	def rate(label, value, unit, stated, note=None):
		return {"label": label, "qty": value, "unit": unit, "kind": "rate",
		        "stated": bool(stated), "note": note or ""}

	# ---- what the field needs
	field_rows = [
		{"label": _("Saleable stems wanted"), "kind": "qty", "unit": _("stems"),
		 "qty": int(round(cint(plants) * saleable)), "stated": True},
		rate(_("Stems a plant gives over its life"), flt(spp, 2), _("stems"), spp),
		rate(_("Fail grading at harvest"), flt(reject, 2), "%", reject,
		     None if reject else _("none stated, so none is allowed for")),
		rate(_("Saleable stems a plant gives"), flt(saleable, 2), _("stems"), True),
		{"label": _("Plants that must stand"), "kind": "qty", "unit": _("plants"),
		 "qty": cint(plants), "stated": True},
	]
	planted = cint(to_plant) or cint(plants)
	bites = bool(field_pct and field_pct < 100)
	field_rows.append(rate(
		_("Establish once planted"), flt(field_pct, 2) if field_pct else 100.0, "%",
		bool(field_pct),
		None if bites else _("none stated, so every plant is assumed to take")))
	groups = [{
		"key": "field", "title": _("Establishment loss"),
		"lead": _("from what is sold back to what goes in the ground"),
		"rows": field_rows, "result_label": _("Plants to plant"),
		"result": planted, "result_unit": _("plants"),
	}]

	# ---- and what propagation has to start with to hand that many over
	prop_rows = [{"label": _("Plants to plant"), "kind": "qty",
	              "unit": _("plants"), "qty": planted, "stated": True}]
	if flt(per_unit) and flt(per_unit) != 1 and not (rooting_pct and rooting_pct < 100):
		# Said without the stage's own name: "15 plants from every Roots" is not
		prop_rows.append(rate(_("Plants from each one bought"), flt(per_unit, 4),
		                      _("plants"), True))
	else:
		roots = bool(rooting_pct and rooting_pct < 100)
		prop_rows.append(rate(
			_("Root in propagation"), flt(rooting_pct, 2) if rooting_pct else 100.0,
			"%", bool(rooting_pct),
			None if roots else _("none stated, so every one bought is assumed to root")))
	groups.append({
		"key": "propagation", "title": _("Propagation loss"),
		"lead": _("what has to be bought to hand that many over"),
		"rows": prop_rows,
		"result_label": (_("{0} needed").format(stage or _("Units"))
		                 if units is not None else _("Nothing is bought")),
		"result": cint(units) if units is not None else None,
		"result_unit": (stage or _("units")).lower(),
		# A standing pool is cut from, not ordered: there is no unit to count.
		"note": (None if units is not None else
		         _("This route is cut from a standing pool, so the plan sizes the "
		           "pool rather than an order.")),
	})

	return {"groups": groups, "stage": stage,
	        "saleable_per_plant": round(saleable, 4),
	        "any_loss_stated": bool(reject or (field_pct and field_pct < 100)
	                                or (rooting_pct and rooting_pct < 100)
	                                or (flt(per_unit) and flt(per_unit) != 1))}


def _units_for(plants, rows, stage):
	"""How many units of `stage` to buy to stand `plants` plants up."""
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
	"""Write the chosen split down, then build what follows from it."""
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
		fields["tc_supplier_allocations"] = (
			allocations if isinstance(allocations, str)
			else frappe.as_json(allocations))
	plan.db_set(fields, update_modified=False)
	# ... and then let the plan work the agreement through to its plantings.
	if not cint(plan.docstatus):
		plan.reload()
		plan.save(ignore_permissions=True)
	frappe.clear_document_cache("Summer Flower Production Plan", plan.name)
	frappe.db.commit()

	out = {"production_plan": plan.name, "tc": tc, "roots": roots,
	       "order_by": first, "steps": []}
	# Same rule as the motherstock card: the decision is written onto the plan
	for label, fn in []:
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
	"""Carry the card's choices onto the procurement plan that now exists."""
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	sell, raise_ = _held(plan, "sf_pending_selling"), _held(plan, "sf_pending_raising")
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": production_plan, "docstatus": 0}, "name")
	if q and (sell or raise_):
		# Both tables in ONE save. Writing the sellers first left the plan with
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
	"""Who sells each way in, and what is already chosen, for the card."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": production_plan, "docstatus": 0},
	                        "name")
	if not q:
		# Nothing bought yet, but the choice is still worth making: the card can
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
	"""Everything the "who raises it" chooser needs, in one call."""
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
	"""Say who raises this plan's material, from the dashboard."""
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
	"""The propagation plan as dates and quantities, and nothing else."""
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
	"""Whether this crop has a motherstock, for the form to offer the right thing."""
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
