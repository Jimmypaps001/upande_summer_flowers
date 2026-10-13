# Copyright (c) 2026, James Kiruga and contributors
"""The endpoints the planning page calls, exercised as the page calls them.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_actuals_api.main

Rolled back.
"""

import traceback

import frappe
from frappe.utils import cint, getdate

PLAN = "SFPROP-2026-00012"
OK, BAD = [], []


def ck(cond, label, detail=""):
	(OK if cond else BAD).append(label)
	print(("  PASS  " if cond else "  FAIL  ") + label + (("   " + str(detail)) if detail else ""))


def main(plan=PLAN):
	try:
		_run(plan)
	except Exception:
		print(traceback.format_exc())
	finally:
		frappe.db.rollback()
		print("\nrolled back")
	print("\n%d passed, %d failed" % (len(OK), len(BAD)))
	for b in BAD:
		print("   FAILED:", b)


def _run(name):
	from upande_summer_flowers.summer_flowers import planning_api as api

	frappe.set_user("Administrator")
	d = frappe.get_doc("Summer Flower Propagation Plan", name)

	print("=== the payload the page renders from ===")
	r = api.propagation_detail(propagation_plan=name)
	for k in ("actuals", "consignments", "weeks"):
		ck(k in r, "payload carries %s" % k)
	a = r["actuals"]
	for k in ("recorded", "reported_to", "weeks_closed", "tc_received",
	          "roots_withdrawn", "plants_to_farm", "plants_lost",
	          "carried_short", "survival_pct", "survival_basis", "note",
	          "replan_cover", "replan_still_short", "replan_note"):
		ck(k in a, "  actuals.%s" % k)
	ck(all("state" in w for w in r["weeks"]), "every week row has a state")
	ck(all("promised" in w and "delivered" in w for w in r["weeks"]),
	   "and both sides of the comparison")
	ck(all("actual_units" in c and "recorded_from" in c
	       for c in r["consignments"]), "every consignment row too")
	ck(cint(a["recorded"]) == 0, "nothing recorded yet", a["recorded"])

	print("\n=== recording one, the way the page does ===")
	wk = next(w for w in r["weeks"] if w["deliver_on"]
	          and getdate(w["deliver_on"]) < getdate()
	          and cint(w["promised"] or w["plants_to_stick"]))
	promised = cint(wk["promised"] or wk["plants_to_stick"])
	out = api.record_propagation_actual(
		propagation_plan=name, event="Plants To Farm",
		actual_date=wk["deliver_on"], against_week=wk["deliver_on"],
		plants=int(promised * 0.7), reference="page test")
	ck(bool(out.get("name")), "a record was created", out)
	ck(frappe.db.get_value("Summer Flower Propagation Actual", out["name"],
	                       "docstatus") == 1, "and submitted")

	r2 = api.propagation_detail(propagation_plan=name)
	a2 = r2["actuals"]
	ck(cint(a2["recorded"]) == 1, "the payload sees it", a2["recorded"])
	ck(str(a2["reported_to"]) == str(wk["deliver_on"]),
	   "the cutoff moved", a2["reported_to"])
	row = next(w for w in r2["weeks"] if w["deliver_on"] == wk["deliver_on"])
	ck(cint(row["delivered"]) == int(promised * 0.7),
	   "on the right week", row["delivered"])
	ck(row["state"] == "Part", "and reads Part", row["state"])
	ck(cint(a2["carried_short"]) > 0, "a gap is carried", a2["carried_short"])
	ck(bool(a2["replan_note"]), "and a proposal offered")

	print("\n=== a bad record is refused, not silently dropped ===")
	for bad, why in (
			(dict(event="Loss", plants=10), "a loss with no reason"),
			(dict(event="TC Received", units=0, plants=0), "a movement of nothing"),
			(dict(event="TC Received", units=-5), "a negative quantity")):
		kw = dict(propagation_plan=name, actual_date="2026-05-25")
		kw.update(bad)
		try:
			api.record_propagation_actual(**kw)
			ck(False, "refused: %s" % why)
		except frappe.ValidationError:
			frappe.clear_last_message()
			ck(True, "refused: %s" % why)

	print("\n=== accepting, the way the page does ===")
	try:
		api.accept_propagation_replan(propagation_plan=name)
		ck(cint(a2["replan_cover"]) > 0,
		   "accepted only because there was cover", a2["replan_cover"])
	except frappe.ValidationError as e:
		frappe.clear_last_message()
		ck(cint(a2["replan_cover"]) == 0,
		   "refused because there is nothing to cover it with",
		   frappe.utils.strip_html(str(e))[:70])

	print("\n=== a guest gets nothing ===")
	frappe.set_user("Guest")
	for fn, kw in ((api.propagation_detail, {"propagation_plan": name}),
	               (api.record_propagation_actual,
	                {"propagation_plan": name, "event": "Loss",
	                 "actual_date": "2026-05-25", "plants": 1, "reason": "x"}),
	               (api.accept_propagation_replan, {"propagation_plan": name})):
		try:
			fn(**kw)
			ck(False, "guest blocked from %s" % fn.__name__)
		except frappe.PermissionError:
			frappe.clear_last_message()
			ck(True, "guest blocked from %s" % fn.__name__)
	frappe.set_user("Administrator")
