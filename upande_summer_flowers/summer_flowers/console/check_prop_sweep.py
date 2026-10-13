# Copyright (c) 2026, James Kiruga and contributors
"""Every propagation plan still rebuilds, with the actuals layer on it.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_prop_sweep.main

Rolled back.
"""

import traceback

import frappe
from frappe.utils import cint

OK, BAD = [], []


def ck(cond, label, detail=""):
	(OK if cond else BAD).append(label)
	print(("  PASS  " if cond else "  FAIL  ") + label + (("   " + str(detail)) if detail else ""))


def main():
	try:
		_run()
	except Exception:
		print(traceback.format_exc())
	finally:
		frappe.db.rollback()
		print("\nrolled back")
	print("\n%d passed, %d failed" % (len(OK), len(BAD)))
	for b in BAD:
		print("   FAILED:", b)


def _run():
	for n in frappe.get_all("Summer Flower Propagation Plan", pluck="name",
	                        order_by="name"):
		d = frappe.get_doc("Summer Flower Propagation Plan", n)
		try:
			d.flags.ignore_permissions = True
			d.save()
		except Exception as e:
			ck(False, "%s rebuilds" % n, frappe.utils.strip_html(str(e))[:110])
			continue
		d = frappe.get_doc("Summer Flower Propagation Plan", n)
		ok = (cint(d.actuals_recorded) == 0 and cint(d.carried_short) == 0
		      and cint(d.weeks_closed) == 0 and not d.replan_note)
		ck(ok, "%s rebuilds clean with no actuals" % n,
		   "recorded=%s closed=%s short=%s" % (d.actuals_recorded, d.weeks_closed,
		                                       d.carried_short))
		# The actuals layer must never invent a figure out of an empty ledger.
		ck(not cint(d.plants_to_farm) and not cint(d.tc_received)
		   and not cint(d.roots_withdrawn) and not cint(d.plants_lost),
		   "   and claims no material moved")
		ck(not cint(d.actual_survival_pct),
		   "   and quotes no survival rate", d.actual_survival_pct)
