# Copyright (c) 2026, James Kiruga and contributors
"""The card says where the split came from, and an asked share takes effect.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_split_origin.main

Rolled back.
"""

import traceback

import frappe
from frappe.utils import cint, flt

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
	from upande_summer_flowers.summer_flowers import root_line as rl
	from upande_summer_flowers.summer_flowers import planning_api as api

	# A plan whose agreement is still GOOD FOR the season it was made against.
	# Picking whichever was newest got a stale one -- an agreement for fewer
	# plants than the plan now needs -- which the card deliberately does not
	# adopt, so the test read a correct refusal as a failure.
	name, stale_name = plan, None
	for n in frappe.get_all("Summer Flower Production Plan",
	                        filters={"tc_choice_committed": 1}, pluck="name",
	                        order_by="creation desc"):
		d = frappe.get_doc("Summer Flower Production Plan", n)
		wanted = sum(cint(b.plants) for b in d.plan_blocks
		             if cint(b.is_new_planting))
		v = frappe.get_cached_doc("Crop Protocol Version", d.protocol)
		from upande_summer_flowers.summer_flowers import crop_protocol as cp
		per_root = rl._lift_return(cp.route_rows(v) or []) or 0
		if rl._agreement_stale(d, wanted, per_root):
			stale_name = stale_name or n
		elif not name:
			name = n
	ck(bool(name), "a plan whose agreement still fits the season", name)
	p = frappe.get_doc("Summer Flower Production Plan", name)
	keep = rl._survival("Tissue Culture")

	print("\n=== an asked share takes effect ===")
	seen = {}
	for pct in (40, 50, 65, 80):
		m = rl.arrival_plan(p, tc_share=pct / 100.0)
		seen[pct] = m["suggested"]["tc"]
		ck(abs(flt(m["tc_share_pct"]) - pct) < 0.05,
		   "   %d%% asked -> %s%% reported" % (pct, m["tc_share_pct"]))
		ck(m["split_from"] == "asked", "   and it says the share was asked for",
		   m["split_from"])
		ck("share you asked for" in m["suggested"]["from"],
		   "   in words too")
	ck(len(set(seen.values())) == len(seen),
	   "   every share gives a different order", seen)
	ck(seen[80] > seen[40], "   and more TC means more plantlets")

	print("\n=== the agreed pair reads back as itself ===")
	m = rl.arrival_plan(p)
	agreed = cint(p.get("tc_plants_committed"))
	ck(m["split_from"] == "agreed", "   the origin is the agreement",
	   m["split_from"])
	ck("already agreed" in m["suggested"]["from"], "   and says so")
	ck(m["suggested"]["tc"] == agreed,
	   "   and suggests exactly what was agreed, not a re-grossed figure",
	   "%s vs %s agreed" % ("{:,}".format(m["suggested"]["tc"]),
	                        "{:,}".format(agreed)))
	# The bug this replaces: losses were applied twice.
	ck(m["suggested"]["tc"] != int(agreed / keep),
	   "   losses are not counted a second time",
	   "would have been %s" % "{:,}".format(int(agreed / keep)))
	tc_plants = sum(cint(w["by_tc"]) for w in m["weeks"])
	ck(abs(tc_plants - int(agreed * keep)) <= 1,
	   "   the weeks carry the plants the order delivers, not the order",
	   "%s vs %s" % ("{:,}".format(tc_plants), "{:,}".format(int(agreed * keep))))

	print("\n=== an agreement the plan has outgrown is not adopted ===")
	if stale_name:
		sp = frappe.get_doc("Summer Flower Production Plan", stale_name)
		sm = rl.arrival_plan(sp)
		ck(sm["split_from"] != "agreed",
		   "   %s has outgrown its agreement, so it is not used" % stale_name,
		   sm["split_from"])
		ck("already agreed" not in sm["suggested"]["from"],
		   "   and the card does not claim it was")
		ck(bool(sm.get("committed")),
		   "   but what WAS agreed still comes back, so the card can say so")
	else:
		print("      (no stale agreement on this site to check)")

	print("\n=== the protocol's own split, where it states one ===")
	p2 = frappe.copy_doc(p)
	p2.tc_choice_committed = 0
	p2.tc_plants_committed = 0
	p2.roots_committed = 0
	m2 = rl.arrival_plan(p2)
	ck(m2["split_from"] in ("protocol", "default"),
	   "   with nothing agreed it falls to the protocol", m2["split_from"])
	from upande_summer_flowers.summer_flowers import crop_protocol as cp
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	rows = cp.route_rows(v) or []
	if rl._states_a_split(rows):
		ck(m2["split_from"] == "protocol", "   and names the protocol")
		ck(abs(flt(m2["tc_share_pct"]) - rl._tc_share(rows) * 100) < 0.05,
		   "   with the protocol's own figure",
		   "%s vs %s" % (m2["tc_share_pct"], round(rl._tc_share(rows) * 100, 2)))

	print("\n=== and that figure is now on the protocol card ===")
	det = api.protocol_detail(version=p.protocol)
	row = next((r for r in det["route"] if r.get("stage") == "TC"), None)
	ck(row is not None and "share_pct" in row,
	   "   the route payload carries share_pct", row and row.get("share_pct"))
	ck(any(flt(r.get("share_pct")) for r in det["route"]),
	   "   and it is not blank for this protocol")
