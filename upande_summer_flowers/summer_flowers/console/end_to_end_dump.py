"""Everything the end-to-end report needs, from the documents. Read-only."""

import json

import frappe
from frappe.utils import add_days, cint, flt, getdate


def main(plan="SFPP-2026-00061", out="/tmp/claude-1000/e2e.json"):
	from upande_summer_flowers.summer_flowers import propagation_solver as ps

	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	d = {"plan": p.name, "variety": p.variety, "farm": p.farm,
	     "season": p.season, "protocol": v.name,
	     "status": p.workflow_state or p.status}

	# ---- market demand
	md = frappe.db.get_value("Summer Flower Market Demand",
	                         {"variety": p.variety, "farm": p.farm}, "name")
	d["demand"] = {"name": md, "weeks": []}
	if md:
		doc = frappe.get_doc("Summer Flower Market Demand", md)
		d["demand"].update({
			"total": cint(doc.total_demand_stems),
			"peak": cint(doc.peak_weekly_demand),
			"weeks_covered": cint(doc.weeks_covered),
			"price_per_stem": flt(doc.price_per_stem),
		})
		d["demand"]["weeks"] = [
			{"d": str(r.week_start_date), "stems": cint(r.demand_stems)}
			for r in doc.demand_weeks if r.week_start_date]

	# ---- production plan
	d["production"] = {
		"plants": cint(p.new_plants_required),
		"demand_stems": cint(p.total_demand_stems),
		"production_stems": cint(p.total_production_stems),
		"coverage_pct": flt(p.coverage_pct),
		"peak_sticking": cint(p.peak_weekly_sticking_planned),
		"peak_week": p.peak_sticking_week_planned,
		"blocks": [{"block": b.block, "beds": cint(b.beds),
		            "plants": cint(b.plants),
		            "stick": str(b.sticking_year) + "-W%02d" % cint(b.sticking_week),
		            "plant_date": str(b.planting_date or ""),
		            "area": flt(b.net_area_ha)}
		           for b in p.plan_blocks if cint(b.is_new_planting)],
	}

	# ---- procurement
	proc = frappe.get_all("Summer Flower Procurement Plan",
	                      filters={"production_plan": plan, "docstatus": ["<", 2]},
	                      fields=["name", "docstatus", "motherstock_plan",
	                              "sizing_basis", "total_units_to_order"],
	                      order_by="modified desc", limit=1)
	d["procurement"] = {}
	if proc:
		pr = frappe.get_doc("Summer Flower Procurement Plan", proc[0].name)
		d["procurement"] = {
			"name": pr.name, "docstatus": pr.docstatus,
			"basis": pr.sizing_basis,
			"lines": [{"type": r.line_type, "stage": r.entry_stage,
			           "qty": cint(r.qty_to_order),
			           "order_by": str(r.order_by_date or ""),
			           "needed": str(r.required_at_site_date or "")}
			          for r in pr.requirements],
		}

	# ---- motherstock
	ms = frappe.db.get_value("Summer Flower Motherstock Plan",
	                         {"production_plan": plan}, "name")
	d["motherstock"] = {}
	if ms:
		m = frappe.get_doc("Summer Flower Motherstock Plan", ms)
		d["motherstock"] = {
			"name": m.name, "tc": cint(m.tc_to_order),
			"divert_weeks": cint(m.divert_weeks),
			"generations": cint(m.generations),
			"order_by": str(m.order_by_date or ""),
			"first_cut": str(m.first_cut_date or ""),
			"line_end": str(m.line_end_date or ""),
			"peak_pool": cint(m.peak_pool),
			"to_field": cint(m.plants_to_field),
			"weeks_met": cint(m.weeks_met), "weeks_req": cint(m.weeks_required),
			"gens": [{"n": cint(g.generation), "mothers": cint(g.mothers),
			          "arrivals": cint(g.arrivals),
			          "from": str(g.first_cut_date or ""),
			          "cut_weeks": cint(g.cutting_weeks),
			          "cleared": str(g.expiry_date or "")}
			         for g in m.generation_table],
			"weeks": [{"n": cint(w.week_no), "d": str(w.week_start),
			           "standing": cint(w.mothers_standing),
			           "cut": cint(w.cuttings_cut),
			           "to_mult": cint(w.to_multiplication),
			           "to_field": cint(w.to_field),
			           "need": cint(w.demand), "short": cint(w.shortfall),
			           "event": w.event or ""}
			          for w in m.schedule],
		}

	# ---- planting plan + expected stems
	offsets = v.flush_offsets()
	d["flushes"] = [{"week": cint(o), "stems": flt(s)} for o, s in offsets]
	d["stems_per_plant"] = sum(flt(s) for _o, s in offsets)
	supply = {}
	for b in p.plan_blocks:
		if not cint(b.is_new_planting) or not b.planting_date:
			continue
		for off, per in offsets:
			k = add_days(getdate(b.planting_date), 7 * cint(off))
			supply[str(k)] = supply.get(str(k), 0) + int(round(cint(b.plants) * flt(per)))
	d["expected"] = [{"d": k, "stems": n} for k, n in sorted(supply.items())]

	with open(out, "w") as fh:
		json.dump(d, fh, indent=1)
	print("wrote %s" % out)
	print("  demand weeks %d, blocks %d, ms weeks %d, expected weeks %d"
	      % (len(d["demand"]["weeks"]), len(d["production"]["blocks"]),
	         len(d.get("motherstock", {}).get("weeks", [])), len(d["expected"])))
	print("  tc %s, pool %s, plants %s, stems/plant %.1f"
	      % (d.get("motherstock", {}).get("tc"),
	         d.get("motherstock", {}).get("peak_pool"),
	         d["production"]["plants"], d["stems_per_plant"]))
