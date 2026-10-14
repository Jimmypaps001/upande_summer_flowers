# Copyright (c) 2026, James Kiruga and contributors
"""Approve the Eryngium protocols, so the versions carry the lifts and grades.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.approve_eryngium.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.approve_eryngium.main \\
		--kwargs "{'apply': 1}"

A plan reads the VERSION it was built on, not the protocol. The lift profile
and the grade mix sit on the protocol and reach a plan only through a new
snapshot, which approving is what writes. Plans already built keep the version
they were built on; nothing moves under them.
"""

import traceback

import frappe
from frappe.utils import cint, flt


def main(apply=0, variety="Eryngium"):
	try:
		_run(cint(apply), variety)
	except Exception:
		print(traceback.format_exc())


def _run(apply, variety):
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	names = sorted(frappe.get_all(
		"Crop Protocol",
		filters={"name": ["like", "%%%s%%" % variety],
		         "custom_is_summer_flower": 1}, pluck="name"))
	ready, blocked = [], []
	for name in names:
		doc = frappe.get_doc("Crop Protocol", name)
		lifts = len(doc.get("custom_sf_lifts") or [])
		grades = doc.get("custom_sf_grade_allocation") or []
		tot = sum(flt(r.allocation_pct) for r in grades)
		why = []
		if not lifts:
			why.append("no lifts")
		try:
			cp.check_grades(doc)
		except Exception as e:
			frappe.clear_last_message()
			why.append(frappe.utils.strip_html(str(e))[:40])
		cur = doc.get("custom_sf_current_version")
		print("   %-48s lifts=%-2d grades=%d/%s%%  current=%s" % (
			name[:48], lifts, len(grades), round(tot), cur or "none"))
		(blocked if why else ready).append((name, why))

	if blocked:
		print("\n   blocked:")
		for name, why in blocked:
			print("      %-48s %s" % (name[:48], "; ".join(why)))
	if not apply:
		print("\n   (dry run — %d would be approved, %d blocked)"
		      % (len(ready), len(blocked)))
		return

	done = 0
	for name, _why in ready:
		try:
			res = cp.approve(name)
			print("   APPROVED %-44s -> %s" % (name[:44], res.get("version")))
			done += 1
		except Exception as e:
			frappe.clear_last_message()
			print("   refused  %-44s %s" % (
				name[:44], frappe.utils.strip_html(str(e))[:70]))
	frappe.db.commit()
	print("\n   %d approved." % done)
