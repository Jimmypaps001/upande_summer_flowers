# Copyright (c) 2026, James Kiruga and contributors
"""Lift one and lift two, with the losses taken off.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_lift_losses.main

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
		print("\nrolled back")
	print("\n%d passed, %d failed" % (len(OK), len(BAD)))
	for b in BAD:
		print("   FAILED:", b)


def _run(plan):
	from upande_summer_flowers.summer_flowers import tc_supply as ts
	from upande_summer_flowers.summer_flowers import root_line as rl

	print("=== with nothing set, nothing is allowed for ===")
	frappe.db.set_single_value("Summer Flower Settings", "tc_loss_pct", 0)
	frappe.db.set_single_value("Summer Flower Settings", "roots_loss_pct", 0)
	frappe.db.set_single_value("Summer Flower Settings", "propagation_loss_pct", 0)
	ck(ts.survival("Tissue Culture") == 1.0, "TC survival is 100%",
	   ts.survival("Tissue Culture"))
	ck(ts.survival("Roots") == 1.0, "roots survival is 100%")

	print("\n=== the old single provision still works ===")
	frappe.db.set_single_value("Summer Flower Settings", "propagation_loss_pct", 10)
	ck(abs(ts.survival("Tissue Culture") - 0.90) < 1e-9,
	   "a form with nothing of its own falls back to it",
	   ts.survival("Tissue Culture"))
	ck(abs(ts.survival("Roots") - 0.90) < 1e-9, "both forms do")

	print("\n=== the measured figures, per form ===")
	frappe.db.set_single_value("Summer Flower Settings", "tc_loss_pct", 11.3)
	frappe.db.set_single_value("Summer Flower Settings", "roots_loss_pct", 13.0)
	tc_keep, rt_keep = ts.survival("Tissue Culture"), ts.survival("Roots")
	ck(abs(tc_keep - 0.887) < 1e-9, "TC keeps 88.7%", tc_keep)
	ck(abs(rt_keep - 0.870) < 1e-9, "roots keep 87.0%", rt_keep)
	ck(tc_keep != rt_keep, "and the two are no longer the same number")

	print("\n=== an order is grossed up by its own form's loss ===")
	name = plan or frappe.get_all(
		"Summer Flower Production Plan",
		filters={"variety": ["like", "%ryngium%"]}, pluck="name",
		order_by="creation desc", limit=1)
	name = name[0] if isinstance(name, list) else name
	ck(bool(name), "an Eryngium plan to test on", name)
	p = frappe.get_doc("Summer Flower Production Plan", name)
	m = rl.arrival_plan(p)
	ck(bool(m), "the material plan builds")
	sg = m["suggested"]
	need_tc = sum(cint(w["by_tc"]) for w in m["weeks"])
	need_rt = sum(cint(w["roots_needed"]) for w in m["weeks"])
	print("      wants %s plants from TC and %s roots' worth" % (
		"{:,}".format(need_tc), "{:,}".format(need_rt)))
	print("      so orders %s TC and %s roots" % (
		"{:,}".format(sg["tc"]), "{:,}".format(sg["roots"])))
	ck(sg["tc"] >= need_tc, "the TC order covers the need after losses")
	ck(abs(sg["tc"] - need_tc / tc_keep) <= 1,
	   "and is grossed up by the TC figure, not the roots one",
	   "%s vs %.0f" % (sg["tc"], need_tc / tc_keep))
	if need_rt:
		ck(abs(sg["roots"] - need_rt / rt_keep) <= 1,
		   "the roots order is grossed up by the roots figure",
		   "%s vs %.0f" % (sg["roots"], need_rt / rt_keep))
	ck(abs(flt(m["survival_pct"]) - 88.7) < 0.01, "the card reports TC survival",
	   m["survival_pct"])
	ck(abs(flt(m["roots_survival_pct"]) - 87.0) < 0.01,
	   "and roots survival separately", m["roots_survival_pct"])
	ck("11.3" in (sg.get("from") or "") and "13" in (sg.get("from") or ""),
	   "and says both out loud", sg.get("from"))

	print("\n=== lift one and lift two ===")
	waves = ts.handover_waves("Tissue Culture", None, 7)
	print("      TC waves:", waves)
	ck(len(waves) >= 2, "a consignment comes off in more than one lift", len(waves))
	batch = {"tc": 100000, "week": "2027-05-24", "week_iso": "2027-W21",
	         "arrives": "2027-04-05", "batch": 1}
	lifts = ts._lift_batch(dict(batch), waves, tc_keep)
	for l in lifts:
		print("      lift %s  +%sw  %s%%  %s plants" % (
			l["lift"], l["weeks_after_arrival"], l["share_pct"],
			"{:,}".format(l["plants"])))
	ck(len(lifts) >= 2, "both lifts are produced", len(lifts))
	total = sum(cint(l["plants"]) for l in lifts)
	ck(total == int(100000 * tc_keep),
	   "the lifts add up to the consignment AFTER losses, not before",
	   "%s vs %s (gross %s)" % (total, int(100000 * tc_keep), 100000))
	ck(total < 100000, "which is fewer than were sent", total)
	ck(lifts[0]["plants"] > lifts[-1]["plants"],
	   "lift one is the bigger of the two")
	ck(len({l["lift"] for l in lifts}) == len(lifts), "each lift is numbered once")
	ck(all(l["weeks_after_arrival"] > 0 for l in lifts),
	   "and each one lands a stated number of weeks after arrival")
