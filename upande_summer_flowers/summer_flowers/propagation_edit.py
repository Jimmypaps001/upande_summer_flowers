"""Who propagates a plan's material, and what they deliver to the farm.

Choosing is all that is asked: an own propagation unit or an outside
propagator. Everything else has a default -- equal shares between those
chosen, the propagator's own lead time and rate per plant -- and their
deliveries to the farm follow the plan's plantings: each planting week,
each propagator hands over its share of that week's plants, started its
lead time before.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from upande_summer_flowers.summer_flowers import supply_cycles as sc


def _doc(plan, write=False):
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write" if write else "read",
	                      doc, throw=True)
	return doc


def _state(doc):
	from upande_summer_flowers.summer_flowers import root_line as rl

	try:
		choices = rl.raising_choices(doc.name) or {}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "raising choices %s" % doc.name)
		choices = {}
	units = [{"name": u["name"], "kind": "unit", "farm": u.get("farm"),
	          "free": cint(u.get("free")), "capacity": cint(u.get("capacity")),
	          "here": cint(u.get("here"))} for u in (choices.get("units") or [])]
	outside = [{"name": p["name"], "kind": "outside", "lead_weeks": cint(p.get("lead_weeks")),
	            "rate": flt(p.get("rate"))} for p in (choices.get("propagators") or [])]
	kinds = {u["name"]: "unit" for u in units}
	kinds.update({p["name"]: "outside" for p in outside})
	chosen = [{"party": r.party, "kind": "outside" if (r.alloc_type or "").startswith("Outside")
	           else "unit", "share_pct": flt(r.share_pct), "plants": cint(r.plants),
	           "rate": flt(r.rate_per_plant), "amount": flt(r.amount),
	           "lead_weeks": cint(r.lead_weeks),
	           "propagation_plan": r.get("propagation_plan"),
	           "plan_status": (frappe.db.get_value("Summer Flower Propagation Plan",
	                                               r.get("propagation_plan"), "status")
	                           if r.get("propagation_plan") else None)}
	          for r in (doc.get("plan_raisers") or [])]
	arrivals = [{"week": a.week_iso, "party": a.party, "plants": cint(a.plants_handed),
	             "standing": cint(a.plants_standing), "start": a.start_week}
	            for a in (doc.get("plan_arrivals") or [])]
	return {"plan": doc.name, "docstatus": doc.docstatus,
	        "plants": sum(cint(b.get("plants_to_plant")) or cint(b.plants)
	                      for b in (doc.get("plan_blocks") or []) if cint(b.is_new_planting)),
	        "units": units, "outside": outside, "chosen": chosen, "arrivals": arrivals,
	        "cost": flt(doc.get("propagation_cost"))}


@frappe.whitelist()
def propagation(plan):
	return _state(_doc(plan))


@frappe.whitelist()
def set_propagators(plan, picks):
	"""Save who propagates. picks: [{party, kind, share_pct?, rate?}].

	Shares left out are split evenly over what is left of 100; rate and lead
	time default to the propagator's own."""
	doc = _doc(plan, write=True)
	picks = frappe.parse_json(picks) if isinstance(picks, str) else (picks or [])
	picks = [p for p in picks if (p.get("party") or "").strip()]
	state = _state(doc)
	outside = {p["name"]: p for p in state["outside"]}
	given = sum(flt(p.get("share_pct")) for p in picks if p.get("share_pct") not in (None, ""))
	blank = [p for p in picks if p.get("share_pct") in (None, "")]
	rest = max(0.0, 100.0 - given)
	each = round(rest / len(blank), 4) if blank else 0
	rows = []
	for p in picks:
		name = p["party"].strip()
		kind = p.get("kind") or ("outside" if name in outside else "unit")
		share = flt(p.get("share_pct")) if p.get("share_pct") not in (None, "") else each
		row = {"share_pct": share}
		if kind == "outside":
			row["supplier"] = name
			row["rate_per_plant"] = (flt(p.get("rate")) if p.get("rate") not in (None, "")
			                         else flt((outside.get(name) or {}).get("rate")))
		else:
			row["propagation_unit"] = name
		rows.append(row)
	sc.save_raisers(doc.name, rows)
	doc = frappe.get_doc("Summer Flower Production Plan", doc.name)
	drafts = draft_propagation_plans(doc)
	carry_to_procurement(doc.name)
	doc.reload()
	out = _state(doc)
	out["drafts"] = drafts
	return out


def carry_to_procurement(plan_name):
	"""Put the plan's propagators on its draft procurement plan.

	The procurement plan will not be submitted until it says who raises what
	it buys; the plan's Propagation tab is where that is chosen."""
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": plan_name, "docstatus": 0}, "name")
	if not q:
		return None
	rows = frappe.get_all("Summer Flower Plan Raiser", filters={"parent": plan_name},
	                      fields=["alloc_type", "party", "share_pct", "rate_per_plant", "raises"],
	                      order_by="idx")
	if not rows:
		return None
	proc = frappe.get_doc("Summer Flower Procurement Plan", q)
	stage = proc.main_entry_stage() if hasattr(proc, "main_entry_stage") else None
	proc.set("propagation_allocation", [])
	for r in rows:
		outside = (r.alloc_type or "").startswith("Outside")
		proc.append("propagation_allocation", {
			"alloc_type": "Outside Propagator" if outside else "Own Unit",
			"raises": r.raises or stage,
			"propagation_unit": None if outside else r.party,
			"supplier": r.party if outside else None,
			"share_pct": flt(r.share_pct),
			"rate_per_plant": flt(r.rate_per_plant) if outside else 0})
	proc.flags.ignore_permissions = True
	proc.save()
	return proc.name


def draft_propagation_plans(doc):
	"""A draft propagation plan for each of our own units chosen, kept in step.

	An outside propagator gets none -- it is bought from on an order. A unit no
	longer chosen has its untouched draft withdrawn; one that has had anything
	raised off it stays, as the record of what was asked."""
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_propagation_plan.summer_flower_propagation_plan import (
		build_from_plan,
	)

	if cint(doc.docstatus) != 0:
		return {"drafted": [], "withdrawn": []}
	# The builder reads the plan from the cache; the propagators were just saved.
	frappe.clear_document_cache("Summer Flower Production Plan", doc.name)
	made, wanted = [], set()
	for r in doc.get("plan_raisers") or []:
		if (r.alloc_type or "").startswith("Outside") or not r.party:
			continue
		wanted.add(r.party)
		try:
			res = build_from_plan(doc.name, as_dict=True, propagation_unit=r.party)
		except Exception as e:
			frappe.log_error(frappe.get_traceback(), "Draft propagation plan %s %s" % (doc.name, r.party))
			made.append({"unit": r.party, "error": frappe.utils.strip_html(str(e))[:140]})
			continue
		frappe.db.set_value("Summer Flower Plan Raiser", r.name, "propagation_plan", res["name"],
		                    update_modified=False)
		made.append({"unit": r.party, "plan": res["name"], "created": res.get("created")})
	gone = []
	for d in frappe.get_all("Summer Flower Propagation Plan",
	                        filters={"production_plan": doc.name, "status": "Draft"},
	                        fields=["name", "propagation_unit", "seedling_requests_created",
	                                "motherstock_batches_created"]):
		if d.propagation_unit in wanted:
			continue
		if cint(d.seedling_requests_created) or cint(d.motherstock_batches_created):
			continue
		frappe.delete_doc("Summer Flower Propagation Plan", d.name, ignore_permissions=True, force=True)
		gone.append(d.name)
	return {"drafted": made, "withdrawn": gone}
