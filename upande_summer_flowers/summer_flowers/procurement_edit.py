"""The procurement plan from the production plan's own Procurement Plan tab.

Saving there keeps a draft Summer Flower Procurement Plan in step with the
plan's suppliers and deliveries; approving the production plan submits it.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


def _doc(plan, write=False):
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write" if write else "read",
	                      doc, throw=True)
	return doc


def _state(doc):
	rows = []
	for q in frappe.get_all("Summer Flower Procurement Plan",
	                        filters={"production_plan": doc.name},
	                        fields=["name", "docstatus", "status", "method", "entry_stage",
	                                "modified"], order_by="creation desc"):
		d = frappe.get_doc("Summer Flower Procurement Plan", q.name)
		rows.append({
			"name": q.name, "docstatus": q.docstatus,
			"status": q.status or ("Draft" if q.docstatus == 0 else
			                       "Submitted" if q.docstatus == 1 else "Cancelled"),
			"method": q.method, "entry_stage": q.entry_stage,
			"sellers": [{"supplier": r.supplier, "share_pct": flt(r.share_pct),
			             "lead_weeks": cint(r.lead_weeks)}
			            for r in (d.get("purchase_allocation") or []) if r.supplier],
			"raisers": [{"party": r.party or r.propagation_unit or r.supplier,
			             "share_pct": flt(r.share_pct), "plants": cint(r.plants)}
			            for r in (d.get("propagation_allocation") or [])],
			"lines": len(d.get("requirements") or []),
			"modified": str(q.modified),
		})
	cons = doc.get("plan_consignments") or []
	return {"plan": doc.name, "docstatus": doc.docstatus, "procurement": rows,
	        "deliveries": len(cons), "units": sum(cint(c.units) for c in cons),
	        "suppliers": sorted({c.supplier for c in cons if c.supplier})}


@frappe.whitelist()
def procurement(plan):
	return _state(_doc(plan))


@frappe.whitelist()
def save_draft(plan):
	"""Create or rebuild the draft procurement plan from this plan as it stands."""
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_procurement_plan.summer_flower_procurement_plan import (
		build,
	)

	doc = _doc(plan, write=True)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("{0} is approved; its procurement plan was submitted with it.").format(doc.name))
	if not (doc.get("plan_consignments") or doc.get("plan_supply")):
		frappe.throw(_("Choose the suppliers and their deliveries first, on Your plan."))
	frappe.flags.draft_procurement = True
	try:
		name = build(doc.name, replace=1)
	finally:
		frappe.flags.draft_procurement = False
	# The sellers are the plan's own suppliers and shares.
	from upande_summer_flowers.summer_flowers import supply_cycles as sc

	picks = {}
	for r in doc.get("plan_supply") or []:
		if r.supplier and r.supplier not in picks:
			picks[r.supplier] = {"supplier": r.supplier, "share_pct": flt(r.share_pct),
			                     "lead_weeks": cint(r.lead_weeks)}
	if picks:
		sc._write_sellers(doc, {"suppliers": list(picks.values())})
	out = _state(frappe.get_doc("Summer Flower Production Plan", doc.name))
	out["saved"] = name
	return out
