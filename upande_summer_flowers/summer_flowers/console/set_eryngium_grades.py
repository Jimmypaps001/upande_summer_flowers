# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Give the Eryngium protocols a grade mix, borrowed and said to be borrowed.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_eryngium_grades.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_eryngium_grades.main \\
		--kwargs "{'apply': 1}"

There is no Eryngium grade mix. Not on any of the eight Eryngium demand
registers, not on any Eryngium protocol, and not in either workbook -- the
only "Grade" in those is Grade 1 and Grade 2 of the lifts, which is the first
and second batch off a consignment and nothing to do with stem length.

So this is Craspedia Gold Drum at Karen's mix, used because somebody at this
farm chose it for a summer flower, and it is written into the change reason as
borrowed so that nobody reading the protocol later takes it for a measurement
of Eryngium. Replace it the moment the real split is known.
"""

import traceback

import frappe
from frappe.utils import cint, flt

MIX = [("50cm", 20.0), ("60cm", 45.0), ("70cm", 35.0)]
SOURCE = ("Grade mix borrowed from Craspedia Gold Drum-Karen (50cm 20%, 60cm "
          "45%, 70cm 35%). No Eryngium grade mix exists on this site or in "
          "the planning workbooks, and an approved protocol has to say how its "
          "stems grade out. Replace this with the real split when it is known.")


def main(apply=0, variety="Eryngium", force=0):
	try:
		_run(cint(apply), variety, cint(force))
	except Exception:
		print(traceback.format_exc())


def _run(apply, variety, force):
	total = sum(p for _g, p in MIX)
	if abs(total - 100) > 0.01:
		print("The mix comes to %s%%, not 100. Refusing." % total)
		return
	for grade, _p in MIX:
		if not frappe.db.exists("Summer Flower Grade", grade):
			print("No Summer Flower Grade called %r on this site." % grade)
			return

	names = frappe.get_all("Crop Protocol",
	                       filters={"name": ["like", "%%%s%%" % variety],
	                                "custom_is_summer_flower": 1},
	                       pluck="name")
	todo = []
	for name in sorted(names):
		have = frappe.get_all("Summer Flower Protocol Grade",
		                      filters={"parent": name,
		                               "parenttype": "Crop Protocol"},
		                      fields=["grade", "allocation_pct"])
		if have and not force:
			print("   %-48s already states %d grades — leaving it" % (
				name[:48], len(have)))
			continue
		todo.append(name)
		print("   %-48s %s" % (name[:48],
		                       ", ".join("%s %s%%" % g for g in MIX)))

	if not todo:
		print("\nNothing to do.")
		return
	if not apply:
		print("\n(dry run — %d protocols would get the mix)" % len(todo))
		return

	for name in todo:
		doc = frappe.get_doc("Crop Protocol", name)
		doc.set("custom_sf_grade_allocation", [])
		for grade, pct in MIX:
			doc.append("custom_sf_grade_allocation",
			           {"grade": grade, "allocation_pct": pct})
		doc.custom_sf_change_reason = SOURCE
		doc.flags.ignore_permissions = True
		doc.save()
	frappe.db.commit()
	print("\n%d protocols now state a grade mix, each recording where it came "
	      "from." % len(todo))
	d = frappe.get_doc("Crop Protocol", todo[0])
	print("\n   %s" % todo[0])
	for r in d.custom_sf_grade_allocation:
		print("      %-8s %6s%%" % (r.grade, r.allocation_pct))
	print("      total %s%%" % sum(flt(r.allocation_pct)
	                               for r in d.custom_sf_grade_allocation))
