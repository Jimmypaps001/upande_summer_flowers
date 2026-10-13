# Copyright (c) 2026, James Kiruga and contributors
"""The lab picker works on a site with no Supplier workflow.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_tc_suppliers.main

marginpar has no custom fields on Supplier at all, so asking for
workflow_state took the whole endpoint down with "Unknown column" and the card
showed no labs with nothing saying why. Rolled back.
"""

import traceback

import frappe
from frappe.utils import cint

OK, BAD = [], []


def ck(c, label, detail=""):
	(OK if c else BAD).append(label)
	print(("  PASS  " if c else "  FAIL  ") + label + (("   " + str(detail)) if detail else ""))


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
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	here = frappe.db.has_column("Supplier", "workflow_state")
	print("   this site HAS a Supplier workflow_state column:", here)

	r = ts.tc_suppliers(all=1)
	ck(isinstance(r, dict) and "names" in r, "the picker returns a list", len(r["names"]))
	ck(len(r["names"]) > 0, "and it is not empty", len(r["names"]))
	ck("pending" in r, "and says which are pending", len(r["pending"]))

	# The column-absent path, which is marginpar's.
	real = frappe.db.has_column
	frappe.db.has_column = lambda dt, col: False if col == "workflow_state" else real(dt, col)
	try:
		r2 = ts.tc_suppliers(all=1)
		ck(len(r2["names"]) == len(r["names"]),
		   "with no workflow_state column it returns the same labs",
		   "%d vs %d" % (len(r2["names"]), len(r["names"])))
		ck(r2["pending"] == r["pending"],
		   "and still flags the pending ones off `disabled` alone")
	finally:
		frappe.db.has_column = real

	s = ts.tc_suppliers(search=r["names"][0][:4])
	ck(any(r["names"][0] == n for n in s["names"]), "search still finds a lab",
	   s["names"][:3])
	ck(len(ts.tc_suppliers()["names"]) <= 40, "the unpaged call is still capped at 40")
