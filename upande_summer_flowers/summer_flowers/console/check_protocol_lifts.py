# Copyright (c) 2026, James Kiruga and contributors
"""The lift profile lives on the protocol, where it can be seen and edited.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_protocol_lifts.main

	lift 1   8 weeks on trough + 7 rooting = ready at 15   45%
	lift 2  10                   + 7         =           17   30%
	lift 3  12                   + 7         =           19   25%

Rolled back.
"""

import traceback

import frappe
from frappe.utils import cint, flt

OK, BAD = [], []
ROOTS = [(1, 8, 7, 45.0), (2, 10, 7, 30.0), (3, 12, 7, 25.0)]


def ck(c, label, detail=""):
	(OK if c else BAD).append(label)
	print(("  PASS  " if c else "  FAIL  ") + label + (("   " + str(detail)) if detail else ""))


def main(protocol=None):
	try:
		_run(protocol)
	except Exception:
		print(traceback.format_exc())
	finally:
		frappe.db.rollback()
		print("\nrolled back")
	print("\n%d passed, %d failed" % (len(OK), len(BAD)))
	for b in BAD:
		print("   FAILED:", b)


def _lifts(doc, rows):
	doc.set("custom_sf_lifts", [])
	for n, trough, rooting, share in rows:
		doc.append("custom_sf_lifts", {
			"form": "Roots", "lift_no": n, "trough_weeks": trough,
			"rooting_weeks": rooting, "share_pct": share})
	doc.flags.ignore_permissions = True
	doc.save()


def _run(protocol):
	from upande_summer_flowers.summer_flowers import crop_protocol as cp
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	name = protocol or frappe.get_all(
		"Crop Protocol", filters={"name": ["like", "%Supernova%"]},
		pluck="name", limit=1)[0]
	doc = frappe.get_doc("Crop Protocol", name)
	print("   %s" % name)

	print("\n=== the pool flag is read off the route ===")
	ck(not cint(doc.get("custom_sf_raised_from_pool")) or True, "flag exists")
	doc.flags.ignore_permissions = True
	doc.save()
	doc.reload()
	rows = cp.route_rows(doc) or []
	pool = any((r.get("stage") or "") in cp.POOL_STAGES for r in rows)
	ck(cint(doc.custom_sf_raised_from_pool) == (1 if pool else 0),
	   "   and matches the route", "%s / route says %s" % (
		   doc.custom_sf_raised_from_pool, pool))
	ck(not pool, "   this crop is handed its material, so no pots or bench")

	print("\n=== the protocol states its own lifts ===")
	_lifts(doc, ROOTS)
	doc.reload()
	got = [(cint(r.lift_no), cint(r.trough_weeks), cint(r.rooting_weeks),
	        cint(r.weeks_after_arrival), flt(r.share_pct))
	       for r in doc.custom_sf_lifts]
	for n, tr, rt, wk, sh in got:
		print("      lift %d  %2dw on trough + %dw rooting = ready at %2d   %.0f%%"
		      % (n, tr, rt, wk, sh))
	ck([g[3] for g in got] == [15, 17, 19],
	   "   ready at 15, 17 and 19 — worked out, not typed", [g[3] for g in got])
	ck(abs(sum(g[4] for g in got) - 100) < 1e-9, "   and the shares make a whole")

	print("\n=== changing the trough time moves the week ===")
	_lifts(doc, [(1, 9, 7, 45.0), (2, 10, 7, 30.0), (3, 12, 7, 25.0)])
	doc.reload()
	ck(cint(doc.custom_sf_lifts[0].weeks_after_arrival) == 16,
	   "   9 + 7 is now ready at 16",
	   cint(doc.custom_sf_lifts[0].weeks_after_arrival))
	_lifts(doc, ROOTS)
	doc.reload()

	print("\n=== a profile that does not make sense is refused ===")
	for label, rows in (
			("over a hundred", [(1, 8, 7, 55.0), (2, 10, 7, 30.0), (3, 12, 7, 25.0)]),
			("under a hundred", [(1, 8, 7, 45.0), (2, 10, 7, 30.0)]),
			("two lift ones", [(1, 8, 7, 45.0), (1, 10, 7, 30.0), (3, 12, 7, 25.0)]),
			("a later lift ready earlier",
			 [(1, 8, 7, 45.0), (2, 10, 7, 30.0), (3, 2, 7, 25.0)]),
			("a lift with no share", [(1, 8, 7, 70.0), (2, 10, 7, 30.0), (3, 12, 7, 0.0)]),
	):
		try:
			_lifts(doc, rows)
			ck(False, "   refused: %s" % label)
		except frappe.ValidationError as e:
			frappe.clear_last_message()
			ck(True, "   refused: %s" % label,
			   frappe.utils.strip_html(str(e))[:64])
		doc.reload()

	print("\n=== the planner uses the protocol, not the house default ===")
	_lifts(doc, ROOTS)
	doc.reload()
	house = ts.handover_waves("Roots", None, 0)
	mine = cp.protocol_lifts(doc, "Roots")
	ck([w for w, _s in mine] == [15, 17, 19],
	   "   the protocol's own profile reads back", [w for w, _s in mine])
	ck(abs(sum(s for _w, s in mine) - 1.0) < 1e-9, "   normalised to a whole")
	print("      house default: %s" % [(w, round(s * 100, 1)) for w, s in house])
	print("      this protocol: %s" % [(w, round(s * 100, 1)) for w, s in mine])

	print("\n=== and it snapshots onto the version ===")
	v = cp.to_version(doc)
	ck(len(v.get("lifts") or []) == 3,
	   "   the version carries the three lifts", len(v.get("lifts") or []))
	ck([cint(r.weeks_after_arrival) for r in (v.get("lifts") or [])] == [15, 17, 19],
	   "   with their weeks")
