# Copyright (c) 2026, James Kiruga and contributors
"""The tissue culture programme is judged on the plants it is responsible for.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_tc_cover.main

A plan raised two ways splits every planting week between them. Crediting TC
lifts against the WHOLE week reported a programme that never caught up and a
season covered only to March -- a shortfall that was simply the roots doing
their job. Rolled back.
"""

import traceback

import frappe
from frappe.utils import cint, flt, getdate

OK, BAD = [], []


def ck(c, label, detail=""):
	(OK if c else BAD).append(label)
	print(("  PASS  " if c else "  FAIL  ") + label + (("   " + str(detail)) if detail else ""))


def main(plan=None):
	try:
		_run(plan)
	except Exception:
		print(traceback.format_exc())
	finally:
		frappe.db.rollback()
	print("\n%d passed, %d failed" % (len(OK), len(BAD)))
	for b in BAD:
		print("   FAILED:", b)


def _run(plan):
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	names = [plan] if plan else frappe.get_all(
		"Summer Flower Production Plan", pluck="name", order_by="creation desc")
	done = 0
	for name in names:
		p = frappe.get_doc("Summer Flower Production Plan", name)
		allocs = p.get("tc_supplier_allocations")
		if not allocs:
			continue
		target = cint(p.get("tc_plants_committed")) or None
		b = ts.batch_plan(p, allocs, tc_target=target)
		if not b or not b.get("handovers"):
			continue
		done += 1
		keep = ts.survival("Tissue Culture")
		lifts = b["handovers"]
		made = sum(cint(x["plants"]) for x in lifts)
		served = sum(cint(x["plants_served"]) for x in lifts)
		# What the LABS WERE GIVEN is what turns into plants. The target is what
		# they were meant to be given, and the two differ whenever a plan is
		# over- or under-allocated -- which is the thing short_by and over_by
		# are for, and not something the lift arithmetic should hide.
		want = int(round(cint(b["tc_allocated"]) * keep))
		tc_need = int(round(cint(b["tc_target"]) * keep))

		print("\n   %s  %s" % (name, p.variety))
		print("      target %s, allocated %s -> %s plants, %s credited, "
		      "season wants %s" % (
			"{:,}".format(cint(b["tc_target"])), "{:,}".format(cint(b["tc_allocated"])),
			"{:,}".format(made), "{:,}".format(served),
			"{:,}".format(cint(b["plants_needed"]))))

		ck(made == want,
		   "   lifts yield exactly what was allocated, after losses",
		   "%s vs %s" % (made, want))
		ck(served <= made, "   nothing is credited that was not delivered")
		ck(served <= tc_need + 1,
		   "   and nothing is credited beyond the TC share of the season",
		   "%s vs %s" % (served, tc_need))
		# The point of the fix: the programme must be able to finish. Measured
		# against the roots' share too, it never could.
		spare = max(0, made - tc_need)          # the over-allocation, if any
		ck(made - served - spare <= max(1, int(tc_need * 0.02)),
		   "   everything it was responsible for is credited",
		   "%s uncredited beyond the %s over-allocated" % (
			   "{:,}".format(made - served - spare), "{:,}".format(spare)))
		ck(cint(b["plants_needed"]) >= made or b.get("over_by"),
		   "   the season total still reports the whole requirement")
		covered = [x for x in lifts if x.get("serves_to")]
		ck(bool(covered), "   the lifts say how far they take the season")
		if covered:
			last = max(getdate(x["serves_to"]) for x in covered)
			print("      covered to %s" % last)
		ck(all(cint(x["plants"]) >= 0 for x in lifts), "   no negative lift")
		if done >= 3:
			break
	ck(done > 0, "plans with an allocation were tested", done)
