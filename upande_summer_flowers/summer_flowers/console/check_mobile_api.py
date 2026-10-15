# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The mobile surface: every name the Upande Propagation app calls.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_mobile_api.main

The app ships separately from the server and cannot be made to follow a
refactor. So the names in upande_summer_flowers.mobile.api are a contract, and
this is the thing that notices when one is moved, renamed or quietly dropped --
which from the phone looks like a blank screen and nothing in the log.

It also checks the delegation still lines up: a wrapper whose arguments have
drifted from the function behind it fails at call time, on a handset, with a
TypeError nobody sees.
"""

import inspect

import frappe

from upande_summer_flowers.mobile import api as mobile

# What the app asks for, by name. Adding a call to the app means adding it here.
EXPECTED = (
	"propagation_units",
	"propagation_plans",
	"production_plans",
	"propagation_detail",
	"record_propagation_actual",
	"protocol_detail",
	"save_protocol",
	"submit_protocol_for_approval",
	"motherstock_line",
	"motherstock_batches",
	"tc_suppliers",
	"open_plant_orders",
	"draft_plant_receipt",
	"propagation_varieties",
	"propagation_schedule",
)

# A wrapper and the thing it delegates to. The wrapper may offer fewer
# arguments than the real function; it may never offer one that does not exist.
DELEGATES = {
	"production_plans": ("planning_api", "plans"),
	"propagation_detail": ("planning_api", "propagation_detail"),
	"record_propagation_actual": ("planning_api", "record_propagation_actual"),
	"protocol_detail": ("planning_api", "protocol_detail"),
	"save_protocol": ("planning_api", "save_protocol"),
	"motherstock_line": ("planning_api", "motherstock_line"),
	"motherstock_batches": ("planning_api", "motherstock_batches"),
	"submit_protocol_for_approval": ("crop_protocol", "submit_for_approval"),
	"tc_suppliers": ("tc_supply", "tc_suppliers"),
	"open_plant_orders": ("plant_receipt", "open_plant_orders"),
	"draft_plant_receipt": ("plant_receipt", "draft_plant_receipt"),
}

PASS, FAIL = [], []


def _ok(name, cond, detail=""):
	(PASS if cond else FAIL).append((name, detail))


def main():
	for name in EXPECTED:
		fn = getattr(mobile, name, None)
		_ok("%s exists" % name, callable(fn))
		if callable(fn):
			_ok("%s is whitelisted" % name, fn in frappe.whitelisted)

	for wrapper, (module_name, inner_name) in DELEGATES.items():
		fn = getattr(mobile, wrapper, None)
		module = frappe.get_module(
			"upande_summer_flowers.summer_flowers.%s" % module_name)
		inner = getattr(module, inner_name, None)
		if not callable(fn) or not callable(inner):
			_ok("%s delegates to %s.%s" % (wrapper, module_name, inner_name), False,
				"one of them is missing")
			continue
		mine = set(inspect.signature(fn).parameters)
		theirs = set(inspect.signature(inner).parameters)
		extra = mine - theirs
		_ok("%s passes nothing %s.%s will not take" % (wrapper, module_name, inner_name),
			not extra, ", ".join(sorted(extra)))

	# The inner arrival functions moved behind the mobile module. Two whitelisted
	# names for one function is two things to keep in step.
	receipt = frappe.get_module(
		"upande_summer_flowers.summer_flowers.plant_receipt")
	for inner_name in ("open_plant_orders", "draft_plant_receipt"):
		inner = getattr(receipt, inner_name)
		_ok("plant_receipt.%s is not exposed twice" % inner_name,
			inner not in frappe.whitelisted)

	# ---------------------------------------------------- what the unit sees
	# These two back the unit's own screens. The checks are about what they must
	# NOT carry as much as what they must: a propagation unit that can see
	# coverage starts arguing with a plan that was approved without it.
	from upande_summer_flowers.summer_flowers import planning_api as pa

	d = pa.delivery_schedule(weeks=12)
	_ok("delivery schedule answers", isinstance(d, dict) and "weeks" in d)
	_ok("delivery schedule says why it is empty when it is",
	    bool(d.get("rows")) or bool(d.get("reason")), str(d.get("reason") or ""))
	_ok("delivery schedule carries no demand figures",
	    not any(k in d for k in ("total_demand_stems", "coverage_pct", "demand")),
	    ", ".join(sorted(d)))
	for r in d.get("rows", [])[:1]:
		_ok("a delivery row names a farm and a date",
		    bool(r.get("farm")) and bool(r.get("deliver_on")))
		_ok("a delivery row says how the plants are raised",
		    isinstance(r.get("source"), dict))

	# A draft is a proposal, not a load.
	drafts = set(frappe.get_all("Summer Flower Propagation Plan",
	                            filters={"status": ["!=", "Approved"]}, pluck="name"))
	_ok("no draft plan reaches the lorry list",
	    not (drafts & {r["plan"] for r in d.get("rows", [])}))

	with_ms = 0
	for v in pa.propagation_varieties():
		variety = v["variety"]
		m = pa.motherstock_plan(variety=variety)
		_ok("motherstock_plan answers for %s" % variety, isinstance(m, dict))
		if m.get("batches"):
			with_ms += 1
			kinds = {s["kind"] for s in m["steps"]}
			_ok("%s says when it is cut" % variety,
			    bool(kinds & {"ms_first_cut", "ms_full", "ms_cuttable"}),
			    ", ".join(sorted(kinds)))
			cycles = (m["batches"][0] or {}).get("build_up_cycles") or 0
			_ok("%s multiplication weeks match its cycle count" % variety,
			    len([s for s in m["steps"] if s["kind"] == "ms_multiply"]) == cycles,
			    str(cycles))
			sched = pa.propagation_schedule(variety=variety)
			_ok("%s motherstock reaches the calendar" % variety,
			    any(e["kind"].startswith("ms_") for e in sched.get("events", [])))
		else:
			_ok("%s explains having no motherstock" % variety,
			    bool(m.get("reason")), str(m.get("reason") or ""))
	_ok("at least one variety has a motherstock line to check", with_ms > 0, str(with_ms))

	for name, detail in PASS:
		print("  ok   %s%s" % (name, (" -- " + detail) if detail else ""))
	for name, detail in FAIL:
		print("  FAIL %s%s" % (name, (" -- " + detail) if detail else ""))
	print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
	return not FAIL
