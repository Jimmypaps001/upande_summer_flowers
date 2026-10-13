# Copyright (c) 2026, James Kiruga and contributors
"""The protocol card can tell a bought crop from a cut one.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_route_chains.main
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
	print("\n%d passed, %d failed" % (len(OK), len(BAD)))
	for b in BAD:
		print("   FAILED:", b)


def _run():
	from upande_summer_flowers.summer_flowers import planning_api as api

	seen = {}
	for v in frappe.get_all("Crop Protocol Version",
	                        filters={"version_status": "Active"},
	                        fields=["name", "variety"], limit=60):
		try:
			d = api.protocol_detail(version=v.name)
		except Exception:
			frappe.clear_last_message()
			continue
		if "chains" not in d:
			ck(False, "%s carries chains" % v.name)
			continue
		seen[v.name] = d

	ck(bool(seen), "protocols were read", len(seen))

	bought = [n for n, d in seen.items() if not cint(d["has_motherstock"])]
	cut = [n for n, d in seen.items() if cint(d["has_motherstock"])]
	ck(bool(bought), "some crops are bought their material", len(bought))
	print("      bought-in : %d" % len(bought))
	print("      motherstock: %d" % len(cut))

	ery = next((n for n in seen if "ryngium" in n and "Test" not in n), None)
	ck(bool(ery), "an Eryngium protocol is in force", ery)
	if ery:
		d = seen[ery]
		ck(not cint(d["has_motherstock"]),
		   "Eryngium is not raised from a motherstock", d["has_motherstock"])
		ch = d["chains"]
		ck(len(ch) >= 2, "and it has more than one way in", len(ch))
		print("      %s" % ery)
		for c in ch:
			print("         %-12s %-34s lead %2sw + %2sw to ground = %2sw notice%s" % (
				c["entry"], c["label"], c["lead_weeks"], c["weeks_to_ground"],
				c["notice_weeks"],
				("  ×%s per root" % c["returns_per_plant"]) if c["returns_per_plant"] else ""))
		ck(any(c["entry"] == "TC" for c in ch), "one of them is tissue culture")
		ck(any(c["entry"] == "Roots" for c in ch), "and one is roots")
		ck(all(c["label"] for c in ch), "each route reads as a sequence")
		ck(all(c["notice_weeks"] == c["lead_weeks"] + c["weeks_to_ground"]
		       for c in ch), "notice is lead plus time to the ground")

	for n in cut[:1]:
		d = seen[n]
		ck(cint(d["has_motherstock"]), "%s IS cut from a pool" % n[:40])

	# Nothing may vanish: every stage row still appears in exactly one chain.
	for n, d in list(seen.items())[:12]:
		flat = [r for r in d["route"] if r["row_type"] != "Step" and r["stage"]]
		inchains = [s for c in d["chains"] for s in c["stages"]]
		ck(len(flat) == len(inchains),
		   "%s: every stage lands in a chain" % n[:34],
		   "%d vs %d" % (len(flat), len(inchains)))
