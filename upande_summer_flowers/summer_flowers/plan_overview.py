"""Everything a production plan says, in the order the material moves.

One payload for the plan's Overview page and for the planning dashboard, so
the two always tell the same story: how the material is bought (halves,
deliveries, suppliers), how it is raised (route stages, losses, lifts), when
it is planted, and what it cuts against the demand.

Nothing here is specific to a crop. The stages, losses, lifts and flush curve
are read off the plan's protocol version, so a seed crop, a rooted cutting, a
TC crop and a crop cut from its own mother stock all come out of the same code.
"""

import math

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers import supply_cycles as sc


def _week_label(d):
	if not d:
		return ""
	d = getdate(d)
	y, w, _x = d.isocalendar()
	return "%s-W%02d" % (y, w)


def _chains(v):
	"""Each way the material comes in: its share and its stages in order.

	Most crops have one. A crop bought partly as TC and partly as roots has
	two, each with its own stages, losses and timing."""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	out = []
	for chain in cp.route_chains(v):
		stages = [{
			"stage": st.stage,
			"weeks": cint(st.get("weeks")),
			"loss_pct": flt(st.get("loss_pct")),
			"bought": cint(st.get("is_purchase")),
			"standing": cint(st.get("is_standing")),
		} for st, _steps in chain]
		out.append({"share_pct": flt(chain[0][0].get("share_pct")) if chain else 0.0,
		            "stages": stages})
	if len(out) == 1:
		out[0]["share_pct"] = 100.0
	return out


def _survival_from(route, start):
	"""What survives from the stage at `start` to a standing plant."""
	keep = 1.0
	for r in route[start:]:
		keep *= 1.0 - r["loss_pct"] / 100.0
	return keep


def _lifts(v, stages=None):
	"""The lifts of one way in: the rows whose route names its stages."""
	rows_in = list(v.get("lifts") or [])
	if stages:
		mine = [r for r in rows_in if r.get("route") and
		        [x.strip() for x in str(r.route).split("\u2192")] == stages]
		if mine:
			rows_in = mine
		elif any(r.get("route") for r in rows_in):
			rows_in = [r for r in rows_in if not r.get("route")] or rows_in[:0]
	rows = [{"lift_no": cint(r.get("lift_no")) or i + 1,
	         "weeks_after": cint(r.get("weeks_after_arrival")),
	         "share_pct": flt(r.get("share_pct"))}
	        for i, r in enumerate(rows_in)]
	total = sum(r["share_pct"] for r in rows)
	if not rows or total <= 0:
		return []
	for r in rows:
		r["share_pct"] = round(r["share_pct"] * 100.0 / total, 2)
	return sorted(rows, key=lambda r: r["weeks_after"])


def _picks(doc):
	"""The suppliers the plan has chosen, with their shares and cycles."""
	seen = {}
	for r in doc.get("plan_supply") or []:
		if r.supplier and r.supplier not in seen:
			seen[r.supplier] = {"supplier": r.supplier, "share_pct": flt(r.share_pct),
			                    "cycle_weeks": cint(r.get("cycle_count")),
			                    "lead_weeks": cint(r.lead_weeks)}
	return list(seen.values())


@frappe.whitelist()
def plan_overview(plan):
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	out = {"plan": doc.name, "variety": doc.variety, "farm": doc.farm,
	       "season": doc.season or doc.season_start_year, "status": doc.status,
	       "docstatus": doc.docstatus, "protocol": doc.protocol,
	       "supply_mode": doc.get("supply_mode") or "In two halves",
	       "today": _week_label(nowdate())}
	if not doc.protocol:
		out["empty"] = _("This plan has no protocol version, so there is nothing to show.")
		return out
	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)

	chains = _chains(v)
	for c in chains:
		c["entry"] = next((i for i, r in enumerate(c["stages"]) if r["bought"]), None)
		c["survival"] = round(_survival_from(c["stages"], c["entry"] or 0), 4)
		c["lifts"] = _lifts(v, [x["stage"] for x in c["stages"]])
	main = chains[0] if chains else {"stages": [], "entry": None, "survival": 1.0, "lifts": []}
	route, entry, survive = main["stages"], main["entry"], main["survival"]
	from upande_summer_flowers.summer_flowers import root_line as rl

	try:
		per_unit = flt(rl.plants_per_unit_at_plant(v)) or 1.0
	except Exception:
		per_unit = 1.0
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		_cut_from_a_pool,
	)

	pooled = bool(_cut_from_a_pool(v))
	lifts = main["lifts"]
	curve = sorted(v.flush_offsets() or [], key=lambda o: o[0])
	out["route"] = route
	out["ways"] = [{"share_pct": c["share_pct"], "stages": c["stages"],
	                "buy_stage": c["stages"][c["entry"]]["stage"] if c["entry"] is not None else None,
	                "survival": c["survival"], "lifts": c["lifts"]} for c in chains]
	out["buy_stage"] = route[entry]["stage"] if entry is not None else None
	out["bought"] = entry is not None
	out["pooled"] = pooled
	out["survival"] = round(survive, 4)
	out["plants_per_unit"] = round(per_unit, 4)
	out["lifts"] = lifts
	out["flush"] = [{"weeks_after_planting": cint(o), "stems_per_plant": flt(s)}
	                for o, s in curve]
	out["stems_per_plant_life"] = round(sum(flt(s) for _o, s in curve), 3)

	# ---- buying: halves, suppliers, deliveries ------------------------------
	picks = _picks(doc)
	shares = [p["share_pct"] for p in picks] or None
	cycles = [p["cycle_weeks"] for p in picks] or None
	prop = {}
	if out["bought"]:
		try:
			prop = sc.proposal(doc, v, shares=shares, cycles=cycles) or {}
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Plan overview proposal %s" % doc.name)
			prop = {}
	named = prop.get("named_houses") or []
	houses = cint(prop.get("houses")) or len(picks) or 1
	suppliers = []
	for h in range(houses):
		p = picks[h] if h < len(picks) else {}
		n = named[h] if h < len(named) else {}
		suppliers.append({
			"supplier": p.get("supplier") or n.get("label") or n.get("supplier")
			            or _("Supplier {0}").format(h + 1),
			"share_pct": flt(p.get("share_pct") or n.get("share_pct")
			                 or (100.0 / houses)),
			"cycle_weeks": cint((prop.get("cycles_by_house") or [0] * houses)[h]
			                    if h < len(prop.get("cycles_by_house") or []) else 0),
			"lead_weeks": cint(p.get("lead_weeks") or n.get("lead_weeks")
			                   or prop.get("lead_weeks")),
			"named": bool(p.get("supplier") or n.get("supplier")),
		})
	units = lambda plants: int(math.ceil(flt(plants) / per_unit - 1e-9)) if plants else 0
	halves = []
	for hf in prop.get("halves") or []:
		halves.append({
			"label": hf.get("label"), "from": hf.get("from"), "to": hf.get("to"),
			"plants": cint(hf.get("plants")), "units": units(hf.get("plants")),
			"deliveries": cint(hf.get("deliveries")),
			"per_delivery_units": units(hf.get("per_delivery")),
			"agreed": cint(hf.get("agreed")),
			"per_supplier": [{"deliveries": cint(x.get("deliveries")),
			                  "per_delivery_plants": cint(x.get("per_delivery")),
			                  "per_delivery_units": units(x.get("per_delivery"))}
			                 for x in (hf.get("per_house") or [])],
		})
	deliveries = []
	for r in prop.get("rows") or []:
		parts = [{"supplier": suppliers[h]["supplier"] if h < len(suppliers) else "",
		          "plants": cint(q), "units": units(q)}
		         for h, q in enumerate(r.get("split") or []) if cint(q)]
		deliveries.append({
			"no": r.get("no"), "order_by": r.get("order_by"), "arrives": r.get("arrives"),
			"first_planting": r.get("week"), "half": r.get("half"),
			"plants": cint(r.get("plants")), "units": units(r.get("plants")),
			"past": cint(r.get("past")), "suppliers": parts,
			"lifts": [{"lift_no": L.get("lift_no"), "share_pct": L.get("share_pct"),
			           "week": L.get("week"), "plants": cint(L.get("plants"))}
			          for L in (r.get("lifts") or [])],
		})
	out["suppliers"] = suppliers
	out["halves"] = halves
	out["deliveries"] = deliveries
	out["supply_agreed"] = cint(doc.get("supply_confirmed"))
	out["second_half_revisable_until"] = None
	if len(halves) > 1 and deliveries:
		first_h2 = next((d for d in deliveries if cint(d.get("half")) == 2), None)
		lead = max([s["lead_weeks"] for s in suppliers] or [0])
		if first_h2 and first_h2.get("arrives"):
			at = sc._week_monday(first_h2["arrives"])
			if at:
				out["second_half_revisable_until"] = _week_label(add_days(at, -7 * lead))
	out["totals"] = {
		"plants_to_plant": cint(prop.get("plants")),
		"plants_bought": cint(prop.get("plants_bought")),
		"units_to_buy": units(prop.get("plants_bought")),
		"deliveries": len(deliveries),
		"past_deliveries": cint(prop.get("past_deliveries")),
	}

	# ---- planting ----------------------------------------------------------
	plantings = {}
	for b in doc.get("plan_blocks") or []:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		wk = _week_label(b.planting_date)
		p = plantings.setdefault(wk, {"week": wk, "date": str(getdate(b.planting_date)),
		                              "plants": 0, "plants_to_plant": 0, "beds": 0,
		                              "blocks": [], "too_late": 0, "not_placed": 0,
		                              "first_harvest": None})
		p["plants"] += cint(b.plants)
		p["plants_to_plant"] += cint(b.get("plants_to_plant")) or cint(b.plants)
		p["beds"] += cint(b.beds)
		if b.block and b.block not in p["blocks"]:
			p["blocks"].append(b.block)
		p["too_late"] = p["too_late"] or cint(b.get("too_late"))
		p["not_placed"] = p["not_placed"] or cint(b.get("not_placed"))
		if b.get("first_harvest_year") and b.get("first_harvest_week"):
			p["first_harvest"] = "%s-W%02d" % (b.first_harvest_year, cint(b.first_harvest_week))
	out["plantings"] = [plantings[k] for k in sorted(plantings)]

	# ---- harvest against demand ---------------------------------------------
	weeks = []
	for w in sorted(doc.get("plan_weeks") or [], key=lambda r: (r.year, r.week_no)):
		weeks.append({"week": "%s-W%02d" % (w.year, cint(w.week_no)),
		              "demand": cint(w.demand_stems), "production": cint(w.production_stems),
		              "on_time": cint(w.get("on_time_production_stems"))})
	out["weeks"] = weeks
	out["months"] = [{"label": "%s %s" % ((m.month_name or "")[:3], m.year),
	                  "demand": cint(m.demand_stems), "production": cint(m.production_stems)}
	                 for m in sorted(doc.get("plan_months") or [],
	                                 key=lambda r: (r.year, cint(r.month)))]
	out["figures"] = {
		"demand": cint(doc.total_demand_stems), "production": cint(doc.total_production_stems),
		"coverage_pct": round(flt(doc.coverage_pct), 1),
		"on_time_pct": round(flt(doc.get("on_time_coverage_pct")), 1),
		"weeks_short": cint(doc.weeks_in_deficit),
		"plants": cint(doc.new_plants_required), "beds": cint(doc.new_beds_required),
		"earliest_planting": str(doc.get("earliest_planting_date") or "") or None,
		"too_late_plantings": cint(doc.get("too_late_plantings")),
		"not_placed": cint(doc.plantings_not_placed),
	}

	# ---- one delivery followed from arrival to its first cuts ---------------
	# A crop cut from its own mother stock: the TC builds the stock, it is not planted.
	out["example"] = (None if pooled else
	                  _follow_one(deliveries, lifts, survive, per_unit, curve, route, entry))

	# ---- the key dates ------------------------------------------------------
	def first(seq, key):
		vals = [x.get(key) for x in seq if x.get(key)]
		return min(vals) if vals else None

	def last(seq, key):
		vals = [x.get(key) for x in seq if x.get(key)]
		return max(vals) if vals else None

	cut = [w for w in weeks if w["production"]]
	out["milestones"] = [
		{"label": _("First order"), "week": first(deliveries, "order_by")},
		{"label": _("First delivery arrives"), "week": first(deliveries, "arrives")},
		{"label": _("First planting"), "week": first(out["plantings"], "week")},
		{"label": _("Season's first harvest"), "week": cut[0]["week"] if cut else None},
		{"label": _("Last delivery arrives"), "week": last(deliveries, "arrives")},
		{"label": _("Last planting"), "week": last(out["plantings"], "week")},
		{"label": _("Season's last harvest"), "week": cut[-1]["week"] if cut else None},
	]
	return out


def _follow_one(deliveries, lifts, survive, per_unit, curve, route, entry):
	"""The first delivery: how its units become plants and then stems."""
	if not deliveries:
		return None
	d = deliveries[0]
	tc = cint(d.get("units"))
	if not tc:
		return None
	waves = lifts or [{"lift_no": 1, "weeks_after": 0, "share_pct": 100.0}]
	first_after = min(w["weeks_after"] for w in waves)
	start = sc._week_monday(d.get("first_planting"))
	stages = route[(entry or 0) + 1:]
	rows, given = [], 0
	for i, w in enumerate(waves):
		share = w["share_pct"] / 100.0
		got = tc - given if i == len(waves) - 1 else int(round(tc * share))
		given += got
		after = []
		q = float(got)
		for st in stages:
			q *= 1.0 - st["loss_pct"] / 100.0
			after.append({"stage": st["stage"], "plants": int(round(q))})
		rows.append({"lift_no": w["lift_no"], "share_pct": w["share_pct"],
		             "planted": _week_label(add_days(start, 7 * (w["weeks_after"] - first_after)))
		             if start else None,
		             "offset": w["weeks_after"] - first_after,
		             "units": got, "after": after,
		             "standing": int(round(got * survive))})
	cuts = []
	if curve and start:
		weeks_out = {}
		for r in rows:
			for off, spp in curve:
				k = cint(off) + r["offset"]
				weeks_out.setdefault(k, [0.0] * len(rows))
				weeks_out[k][rows.index(r)] += r["standing"] * flt(spp)
		for k in sorted(weeks_out)[:6]:
			parts = [int(round(x)) for x in weeks_out[k]]
			cuts.append({"week": _week_label(add_days(start, 7 * k)), "by_lift": parts,
			             "stems": sum(parts)})
	life = int(round(sum(r["standing"] for r in rows) * sum(flt(s) for _o, s in curve)))
	return {"delivery": d.get("no"), "arrives": d.get("arrives"), "units": tc,
	        "stages": [s["stage"] for s in stages], "lifts": rows, "cuts": cuts,
	        "standing": sum(r["standing"] for r in rows), "life_stems": life}
