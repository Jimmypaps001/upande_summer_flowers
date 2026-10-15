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

	for name, detail in PASS:
		print("  ok   %s%s" % (name, (" -- " + detail) if detail else ""))
	for name, detail in FAIL:
		print("  FAIL %s%s" % (name, (" -- " + detail) if detail else ""))
	print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
	return not FAIL
