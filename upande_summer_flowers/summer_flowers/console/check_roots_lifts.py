# Copyright (c) 2026, James Kiruga and contributors
"""The farm's roots protocol, checked against its own worked example.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_roots_lifts.main

	5,000 roots, fifteen plants each, 75,000 plants
	lift 1   8 wk trough + 7 rooting = 15   45%   33,750
	lift 2  10          + 7         = 17   30%   22,500
	lift 3  12          + 7         = 19   25%   18,750

Rolled back.
"""

import traceback

import frappe
from frappe.utils import cint, flt

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
	print("\n%d passed, %d failed" % (len(OK), len(BAD)))
	for b in BAD:
		print("   FAILED:", b)


def _run():
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	print("=== the profile as stated ===")
	waves = ts.handover_waves("Roots", None, 0)
	for i, (wk, sh) in enumerate(waves, 1):
		print("   lift %d  %2d weeks  %5.1f%%" % (i, wk, sh * 100))
	ck(len(waves) == 3, "roots come off in three lifts", len(waves))
	ck([w for w, _s in waves] == [15, 17, 19], "at 15, 17 and 19 weeks",
	   [w for w, _s in waves])
	ck(abs(sum(s for _w, s in waves) - 1.0) < 1e-9, "and the shares make a whole",
	   sum(s for _w, s in waves))
	ck(abs(waves[0][1] - 0.45) < 1e-9, "45% on the first lift")
	ck(abs(waves[2][1] - 0.25) < 1e-9, "25% on the last")

	print("\n=== the worked example: 5,000 roots ===")
	# 5,000 roots at fifteen plants each. _lift_batch splits what it is given,
	# so it is given the plants rather than the roots.
	batch = {"tc": 5000 * 15, "arrives": "2027-01-04"}
	lifts = ts._lift_batch(dict(batch), waves, 1.0)
	for l in lifts:
		print("   lift %s  +%2dw  %5.1f%%  %8s plants  ready %s" % (
			l["lift"], l["weeks_after_arrival"], l["share_pct"],
			"{:,}".format(l["plants"]), l["ready_iso"]))
	got = [l["plants"] for l in lifts]
	ck(got == [33750, 22500, 18750], "33,750 then 22,500 then 18,750", got)
	ck(sum(got) == 75000, "adding to all 75,000", sum(got))
	ck([l["weeks_after_arrival"] for l in lifts] == [15, 17, 19],
	   "in weeks 15, 17 and 19")

	print("\n=== a profile that does not add up is refused ===")
	s = frappe.get_single("Summer Flower Settings")
	keep = [dict(source=r.source, form=r.form, lift_no=r.lift_no,
	             weeks_after_arrival=r.weeks_after_arrival,
	             share_pct=r.share_pct) for r in (s.handover_waves or [])]

	def rebuild(rows):
		s.set("handover_waves", [])
		for r in rows:
			s.append("handover_waves", r)
		s.flags.ignore_permissions = True
		s.save()

	for label, mutate in (
			("over a hundred", lambda rs: [dict(r, share_pct=flt(r["share_pct"]) + 10)
			                               if r["form"] == "Roots" and r["lift_no"] == 1
			                               else r for r in rs]),
			("under a hundred", lambda rs: [r for r in rs
			                                if not (r["form"] == "Roots"
			                                        and r["lift_no"] == 3)]),
			("two lift ones", lambda rs: [dict(r, lift_no=1)
			                              if r["form"] == "Roots" and r["lift_no"] == 2
			                              else r for r in rs]),
			("a later lift that comes off earlier",
			 lambda rs: [dict(r, weeks_after_arrival=2)
			             if r["form"] == "Roots" and r["lift_no"] == 3 else r
			             for r in rs]),
	):
		try:
			rebuild(mutate([dict(r) for r in keep]))
			ck(False, "   refused: %s" % label)
		except frappe.ValidationError as e:
			frappe.clear_last_message()
			ck(True, "   refused: %s" % label,
			   frappe.utils.strip_html(str(e))[:70])
		s.reload()

	rebuild([dict(r) for r in keep])
	ck(True, "   and the stated profile still saves")
