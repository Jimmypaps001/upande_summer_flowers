"""How much of the motherstock table is the same sentence repeated."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())


def _run():
	from upande_summer_flowers.summer_flowers import planning_api

	plan = planning_api.resolve_plan()
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	r = planning_api.simulate_lifecycle(version=p.protocol, tc_qty=20000,
	                                    order_date=None, to_prop_pct=50,
	                                    max_bench_sqm=400)
	rows = r.get("rows") or []
	if not rows:
		print("no rows; keys:", sorted(r)[:20])
		return
	evs = [e for x in rows for e in (x.get("events") or [])]
	print("weeks: %d   event lines: %d" % (len(rows), len(evs)))
	from collections import Counter
	c = Counter()
	for e in evs:
		txt = e if isinstance(e, str) else (e.get("text") or e.get("label") or str(e))
		# group by the wording, ignoring the numbers in it
		key = "".join("#" if ch.isdigit() else ch for ch in txt)
		c[key] += 1
	for k, n in c.most_common(12):
		print("   %4d x  %s" % (n, k[:88]))
	bench = [x for x in rows if (x.get("bench_sqm") or 0) and x.get("bench_limited")]
	print("\n   weeks the bench was full: %d" % len(bench))
	print("   bench events: %s" % [e for e in evs if "Bench" in str(e)][:3])
	print("   wasted cuttings total: %s"
	      % f"{sum(cint(x.get('wasted') or 0) for x in rows):,}")
