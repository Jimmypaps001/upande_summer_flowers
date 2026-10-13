# Copyright (c) 2026, James Kiruga and contributors
"""A season can be picked whatever the site calls its Fiscal Years.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_season_year.main

Here the Fiscal Years are calendar years -- "2027". On marginpar they are the
July-to-June span -- "2027-2028" -- and cint() reads that as 0, so every
season a planner asked for silently became the current one. Both shapes are
exercised. Rolled back.
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
	from upande_summer_flowers.summer_flowers.planning import season_year

	print("=== the helper itself ===")
	for v, want in (("2027", 2027), ("2027-2028", 2027), ("2027-28", 2027),
	                (2027, 2027), (None, 0), ("", 0), ("rubbish", 0),
	                ("  2029-2030", 2029)):
		ck(season_year(v) == want, "season_year(%r) == %r" % (v, want),
		   season_year(v))
	ck(cint("2027-2028") == 0,
	   "cint still returns 0 for a span name -- that was the bug", cint("2027-2028"))

	print("\n=== a plan built on a span-named Fiscal Year ===")
	# marginpar's shape, set straight onto the field. The Fiscal Year record
	# itself cannot be created here -- a July-to-June year overlaps this site's
	# calendar ones and ERPNext refuses it, which is the whole reason the two
	# sites name them differently -- and set_season does not need it to exist.

	plan = frappe.get_doc("Summer Flower Production Plan",
	                      frappe.get_all("Summer Flower Production Plan",
	                                     pluck="name", limit=1)[0])
	was = plan.season_start_year
	print("   using %s (was season_start_year=%r, season=%r)" % (
		plan.name, was, plan.season))

	for value, want_year, want_season in (("2027", 2027, "2027-28"),
	                                      ("2027-2028", 2027, "2027-28")):
		plan.season_start_year = value
		plan.set_season()
		ck(plan.season == want_season,
		   "season_start_year=%-11r -> season %r" % (value, want_season),
		   plan.season)
		ck(str(plan.season_start_date) == "2027-07-01",
		   "   and the season starts 1 July 2027", plan.season_start_date)
		ck(cint(plan.from_week) == 27, "   and opens on W27", plan.from_week)

	print("\n=== the propagation plan takes the year, not a zero ===")
	from upande_summer_flowers.summer_flowers.planning import season_year as sy
	ck(sy("2027-2028") == 2027,
	   "a propagation plan keyed on this season would find its own records",
	   sy("2027-2028"))
	ck(sy("2027-2028") != 0,
	   "   not season 0, which every variety would have shared")
