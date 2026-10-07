# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""What a plan's weekly variance is, and what it would be if it were resolved.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.variance_check.main \\
		--kwargs "{'plan': 'SFPP-2026-00067'}"
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.variance_check.main \\
		--kwargs "{'plan': 'SFPP-2026-00067', 'apply': 1}"

Without `apply` nothing is kept: the plan is rebuilt in memory, reported against
what it says today, and rolled back. That is the only honest way to compare,
because the comparison is between two answers to the same question and one of
them is already written down.
"""

import frappe
from frappe.utils import cint

from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan \
	.summer_flower_production_plan import _populate, size_planting


def _read(plan):
	weeks = [(cint(w.demand_stems), cint(w.production_stems))
	         for w in plan.plan_weeks]
	demand = sum(d for d, _p in weeks)
	grown = sum(p for _d, p in weeks)
	rows = [b for b in plan.plan_blocks if cint(b.is_new_planting)]
	out = {
		"demand": demand, "grown": grown,
		"pct": (grown / demand * 100) if demand else 0,
		"plantings": len(rows),
		"plants": sum(cint(b.plants) for b in rows),
		"beds": sum(cint(b.beds) for b in rows),
		"early": sum(1 for b in rows if cint(b.get("cuts_before_window"))),
		"worst_over": 0.0, "worst_short": 0.0, "outside": 0, "short_weeks": 0,
	}
	for d, p in weeks:
		if not d:
			continue
		v = (p - d) / d * 100
		out["worst_over"] = max(out["worst_over"], v)
		out["worst_short"] = min(out["worst_short"], v)
		if abs(v) > 10.0001:
			out["outside"] += 1
		if p < d:
			out["short_weeks"] += 1
	return out


def _show(label, r, weeks):
	print(f"  {label}")
	print(f"    production      {r['grown']:>12,}  of {r['demand']:,} wanted"
	      f"   = {r['pct']:.1f}%")
	print(f"    plantings       {r['plantings']:>12,}"
	      f"   plants {r['plants']:,}   beds {r['beds']:,}"
	      + (f"   cutting before the window: {r['early']}" if r["early"] else ""))
	print(f"    weeks           {r['worst_short']:+.1f}% to {r['worst_over']:+.1f}%"
	      f"   outside +/-10%: {r['outside']} of {weeks}"
	      f"   short: {r['short_weeks']}")


def _why(plan):
	"""The two numbers that decide whether the band can be held at all.

	The curve, because a cohort sized on its first productive week is sized on
	whatever that week happens to be worth; and the smallest plantable cohort,
	because a planting larger than the week it is planted for cannot sit inside
	a band however well it is solved.
	"""
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	offs = v.flush_offsets()
	if not offs:
		print("    the protocol projects no harvest at all")
		return
	first = offs[0][1]
	peak = max(o[1] for o in offs)
	total = sum(o[1] for o in offs)
	_beds, step, _area = size_planting(v, 1, cint(v.min_planting_beds_derived) or 1)
	print(f"    {v.name}  ({v.growing_cycle})")
	print(f"    curve           {len(offs)} weeks   first {first:.3f} st/plant"
	      f"   peak {peak:.3f}   mean {total / len(offs):.3f}   life {total:.3f}")
	if first > 0:
		print(f"    sizing on the first week alone would buy "
		      f"{peak / first:.1f}x what the peak week needs")
	print(f"    smallest plantable cohort   {cint(step):,} plants")


def main(plan=None, apply=0, show_weeks=0):
	apply = cint(apply)
	names = [plan] if plan else [
		p.name for p in frappe.get_all("Summer Flower Production Plan",
		                               filters={"docstatus": 0}, fields=["name"])]
	for name in names:
		doc = frappe.get_doc("Summer Flower Production Plan", name)
		weeks = len(doc.plan_weeks)
		print(f"\n{name}  {doc.variety} at {doc.farm}  ({weeks} weeks)")
		_why(doc)
		was = _read(doc)
		_show("as it stands", was, weeks)
		_populate(doc)
		now = _read(doc)
		_show("resolved", now, weeks)
		if was["plants"] and now["plants"]:
			print(f"    plants {now['plants'] - was['plants']:+,}"
			      f"  ({now['plants'] / was['plants'] * 100:.0f}% of before)")
		if cint(show_weeks):
			print("    week      demand      grown   variance")
			for w in doc.plan_weeks:
				d, p = cint(w.demand_stems), cint(w.production_stems)
				v = ((p - d) / d * 100) if d else 0
				print(f"    {w.year}-W{cint(w.week_no):02d} {d:>11,} {p:>10,} {v:>8.1f}%")
		if apply:
			# bench execute swallows the real error and retries the call as an
			# expression, which then fails on a name that was never the problem.
			# Print the traceback here or the actual fault is never seen.
			import traceback
			try:
				doc.save()
				frappe.db.commit()
				print("    saved")
			except Exception:
				frappe.db.rollback()
				traceback.print_exc()
				print("    NOT saved")
		else:
			frappe.db.rollback()
			print("    (rolled back — nothing kept)")
