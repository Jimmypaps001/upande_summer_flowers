# Copyright (c) 2026, James Kiruga and contributors
"""Deriving the row type must not reclassify a single existing route row.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_route_shape.main

600 Stage rows and 98 Step rows are already on this site. The grid no longer
asks for a row type -- a row that names a step is a Step -- and the test of
that is whether every one of those 698 rows still comes out as what it is.
Rolled back.
"""

import traceback
import collections

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
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	rows = frappe.get_all("Crop Material Stage",
	                      filters={"parenttype": "Crop Protocol"},
	                      fields=["name", "parent", "row_type", "step", "stage"],
	                      limit_page_length=0)
	was = collections.Counter(r.row_type or "Stage" for r in rows)
	print("   stored today: %s across %d protocols" % (
		dict(was), len({r.parent for r in rows})))

	# What the rule would make of each row, without touching anything.
	wrong = []
	for r in rows:
		derived = "Step" if (r.step or "").strip() else "Stage"
		if derived != (r.row_type or "Stage"):
			wrong.append((r.parent, r.row_type, r.step, r.stage))
	ck(not wrong,
	   "every one of the %d rows derives to what it is stored as" % len(rows),
	   wrong[:4])
	if wrong:
		by = collections.Counter(w[1] for w in wrong)
		print("      would change: %s" % dict(by))

	steps = [r for r in rows if (r.row_type or "Stage") == "Step"]
	ck(all((r.step or "").strip() for r in steps),
	   "every stored Step names a step", len(steps))
	stages = [r for r in rows if (r.row_type or "Stage") != "Step"]
	ck(not [r for r in stages if (r.step or "").strip()],
	   "and no stored Stage names one", len(stages))

	print("\n=== saving a protocol does not move its rows ===")
	for name in sorted({r.parent for r in rows if (r.row_type or "") == "Step"})[:3]:
		doc = frappe.get_doc("Crop Protocol", name)
		before = [(x.row_type, x.stage, x.step) for x in doc.custom_sf_material_route]
		doc.flags.ignore_permissions = True
		doc.save()
		doc.reload()
		after = [(x.row_type, x.stage, x.step) for x in doc.custom_sf_material_route]
		ck(before == after, "   %s unchanged by a save" % name[:44],
		   "" if before == after else "%s -> %s" % (before[:2], after[:2]))

	print("\n=== the chains still build ===")
	for name in sorted({r.parent for r in rows if (r.row_type or "") == "Step"})[:2]:
		doc = frappe.get_doc("Crop Protocol", name)
		chains = cp.route_chains(doc) or []
		ck(bool(chains), "   %s still has chains" % name[:44], len(chains))
		for chain in chains:
			for stage, steps in chain:
				ck(not (stage.get("step") or "").strip(),
				   "      a chain's stage is never a step")

	print("\n=== the harvest flag follows the rows ===")
	n = frappe.get_all("Crop Protocol", pluck="name", limit=1)[0]
	d = frappe.get_doc("Crop Protocol", n)
	d.flags.ignore_permissions = True
	d.save()
	d.reload()
	ck(cint(d.custom_sf_harvest_is_scheduled) ==
	   (1 if (d.get("custom_sf_flush_schedule") or []) else 0),
	   "   %s: flag matches whether it has flush rows" % n[:40],
	   "%s rows / flag %s" % (len(d.get("custom_sf_flush_schedule") or []),
	                          d.custom_sf_harvest_is_scheduled))
