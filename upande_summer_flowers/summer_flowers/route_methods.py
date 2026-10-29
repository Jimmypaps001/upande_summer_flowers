# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The route as one method per way in."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from upande_summer_flowers.summer_flowers.crop_protocol import (
	ROUTE_ENTRY,
	TAKEN_STAGES as OWN,
)
from upande_summer_flowers.summer_flowers.crop_routes import ROUTES

ROUTE_FIELD = "custom_sf_material_route"
METHODS = (1, 2)


def _f(no, name):
	return "custom_sf_m%d_%s" % (no, name)


def _row(r):
	return {"row_type": "Stage", "stage": r.get("stage"),
	        "weeks": cint(r.get("weeks")), "loss_pct": flt(r.get("loss_pct")),
	        "yields_per_unit": flt(r.get("yields_per_unit")) or 1.0,
	        "returns_per_plant": flt(r.get("returns_per_plant")),
	        "is_standing": cint(r.get("is_standing")),
	        "yields_per_week": flt(r.get("yields_per_week")),
	        "max_cycles": cint(r.get("max_cycles")),
	        "item": r.get("item"), "rate": flt(r.get("rate")),
	        "notes": r.get("notes")}


def _split(rows):
	chains, current = [], []
	for r in rows:
		if current and r.get("stage") in ROUTE_ENTRY:
			chains.append(current)
			current = []
		current.append(r)
	if current:
		chains.append(current)
	return chains


def _stages(doc):
	return [r for r in (doc.get(ROUTE_FIELD) or [])
	        if (r.get("row_type") or "Stage") != "Step"]


def method_is_set(doc, no):
	return bool(doc.get(_f(no, "entry")))


def any_method_set(doc):
	return any(method_is_set(doc, n) for n in METHODS)


def fill_from_route(doc, no):
	route = doc.get(_f(no, "route"))
	if not route:
		return
	stages = ROUTES.get(route)
	if not stages:
		frappe.throw(_("{0} is not a route this farm has.").format(route))
	doc.set(_f(no, "entry"), stages[0])
	doc.set(_f(no, "steps"), [])
	for stage in stages[1:]:
		doc.append(_f(no, "steps"), {"row_type": "Stage", "stage": stage,
		                             "weeks": 0, "loss_pct": 0, "yields_per_unit": 1})
	doc.set(_f(no, "route"), None)


def _steps_under(doc):
	out = {}
	for no, chain in zip(METHODS, _split(list(doc.get(ROUTE_FIELD) or []))):
		idx = -1
		for r in chain:
			if (r.get("row_type") or "Stage") != "Step":
				idx += 1
				continue
			out.setdefault((no, idx), []).append(r.as_dict() if hasattr(r, "as_dict")
			                                     else dict(r))
	return out


def sync_route_from_methods(doc):
	for no in METHODS:
		fill_from_route(doc, no)
	if not any_method_set(doc):
		return

	carried = _steps_under(doc)
	rows = []
	for no in METHODS:
		if not method_is_set(doc, no):
			continue
		entry = doc.get(_f(no, "entry"))
		bought = entry not in OWN
		rows.append(dict(_row({"stage": entry,
		                       "weeks": doc.get(_f(no, "entry_weeks")),
		                       "loss_pct": doc.get(_f(no, "entry_loss_pct"))}),
		                 is_purchase=1 if bought else 0,
		                 lead_weeks=cint(doc.get(_f(no, "lead_weeks"))) if bought else 0,
		                 share_pct=flt(doc.get(_f(no, "share_pct")))))
		rows.extend(carried.get((no, 0), []))
		for i, r in enumerate(doc.get(_f(no, "steps")) or [], 1):
			if not r.get("stage"):
				continue
			rows.append(dict(_row(r), is_purchase=0, lead_weeks=0, share_pct=0.0))
			rows.extend(carried.get((no, i), []))

	doc.set(ROUTE_FIELD, [])
	for r in rows:
		doc.append(ROUTE_FIELD, r)


def split_route_into_methods(doc):
	rows = _stages(doc)
	if not rows:
		return 0
	chains = _split(rows)
	for no, chain in zip(METHODS, chains):
		head = chain[0]
		doc.set(_f(no, "entry"), head.get("stage"))
		doc.set(_f(no, "entry_weeks"), cint(head.get("weeks")))
		doc.set(_f(no, "entry_loss_pct"), flt(head.get("loss_pct")))
		doc.set(_f(no, "share_pct"), flt(head.get("share_pct")))
		doc.set(_f(no, "lead_weeks"), cint(head.get("lead_weeks")))
		doc.set(_f(no, "steps"), [])
		for r in chain[1:]:
			doc.append(_f(no, "steps"), _row(r))
	if len(chains) > len(METHODS):
		frappe.msgprint(
			_("{0} has {1} ways in and only {2} methods are held. The rest are "
			  "still in the route table and still planned; they are not shown "
			  "above.").format(doc.name, len(chains), len(METHODS)))
	return min(len(chains), len(METHODS))


def sync_methods_from_route(doc):
	if not any_method_set(doc):
		return
	for no, chain in zip(METHODS, _split(_stages(doc))):
		if not method_is_set(doc, no):
			continue
		head = chain[0]
		doc.set(_f(no, "entry_weeks"), cint(head.get("weeks")))
		doc.set(_f(no, "entry_loss_pct"), flt(head.get("loss_pct")))
		for step, r in zip(doc.get(_f(no, "steps")) or [], chain[1:]):
			for k, v in _row(r).items():
				if k not in ("row_type", "stage", "item", "notes"):
					step.set(k, v)


def check_shares(doc):
	if not all(method_is_set(doc, n) for n in METHODS):
		return
	total = sum(flt(doc.get(_f(n, "share_pct"))) for n in METHODS)
	if abs(total - 100) > 0.5:
		frappe.throw(
			_("The two methods take {0}% of the order between them. Every plant "
			  "comes in one way or the other, so they have to make a hundred.")
			.format(round(total, 2)), title=_("Shares do not make a whole"))


@frappe.whitelist()
def apply_catalogue(dry_run=1, farm=None):
	from upande_summer_flowers.summer_flowers.crop_protocol import PROTOCOL_DOCTYPE
	from upande_summer_flowers.summer_flowers.crop_routes import (
		_lookup, lead_weeks, second_way,
	)

	dry_run = int(dry_run or 0)
	by_variety, _clash = _lookup()
	ways = second_way()
	filters = {"custom_is_summer_flower": 1}
	if farm:
		filters["farm"] = farm

	changed, same, unknown = [], [], []
	for p in frappe.get_all(PROTOCOL_DOCTYPE, filters=filters,
	                        fields=["name", "variety", "farm"]):
		hit = by_variety.get(p.variety)
		if not hit:
			unknown.append(p.variety)
			continue
		route, cycle = hit
		second = ways.get(p.variety)
		doc = frappe.get_doc(PROTOCOL_DOCTYPE, p.name)
		# Compare the entry stages, not the pickers: a picker clears itself once it
		# has filled the method, so reading it back always said "was nothing" and
		# every protocol reported as changed on every run.
		want = ((ROUTES.get(route) or [None])[0],
		        (ROUTES.get(second[0]) or [None])[0] if second else None)
		was = (doc.get(_f(1, "entry")) or None, doc.get(_f(2, "entry")) or None,
		       doc.get("custom_sf_growing_cycle") or None)

		doc.set(_f(1, "route"), route)
		doc.set(_f(2, "route"), second[0] if second else None)
		if not doc.get("custom_sf_growing_cycle"):
			doc.custom_sf_growing_cycle = cycle
		if second:
			if not any(flt(doc.get(_f(n, "share_pct"))) for n in METHODS):
				doc.set(_f(2, "share_pct"), flt(second[1]))
				doc.set(_f(1, "share_pct"), 100.0 - flt(second[1]))
		else:
			if not flt(doc.get(_f(1, "share_pct"))):
				doc.set(_f(1, "share_pct"), 100.0)
			doc.set(_f(2, "share_pct"), 0.0)
		for no, name in ((1, route), (2, second[0] if second else None)):
			if name and not cint(doc.get(_f(no, "lead_weeks"))):
				doc.set(_f(no, "lead_weeks"),
				        cint(lead_weeks().get((ROUTES[name] or [""])[0], 0)))

		now = want + (doc.get("custom_sf_growing_cycle") or None,)
		line = "%s | %s%s | %s" % (p.name, route, " + " + second[0] if second else "",
		                           doc.get("custom_sf_growing_cycle"))
		if was == now:
			same.append(line)
			continue
		changed.append("%s   (was %s%s)" % (line, was[0] or "nothing",
		                                    " + " + was[1] if was[1] else ""))
		if not dry_run:
			doc.flags.ignore_permissions = True
			doc.save()

	if not dry_run:
		frappe.db.commit()
	return {"changed": changed, "already_right": same,
	        "variety_not_in_catalogue": sorted(set(unknown)), "dry_run": bool(dry_run)}
