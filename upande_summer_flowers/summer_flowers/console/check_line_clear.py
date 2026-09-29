"""The line is cleared as one block, so nothing in it outlives the clearance.

A regression check for the rule the farm actually works to: a tranche of
motherstock raised from a later diversion does NOT get a full life of its own,
it dies when the block is cleared, one life after the block's FIRST cut.

Before this rule the pool compounded without bound -- a diverted pool outlived
its parent, its own cuttings outlived it in turn, and a 40% diversion off 1,000
plantlets reached 72 million mother plants.

    bench --site <site> execute ...check_line_clear.main
"""

import frappe
from frappe.utils import cint

DIVERTS = (0, 40, 80)


def main(version=None):
	from upande_summer_flowers.summer_flowers import lifecycle_sim as ls

	versions = ([version] if version else
	            [r.name for r in frappe.get_all(
		            "Crop Protocol Version",
		            filters={"max_multiplication_cycles": [">", 0]},
		            fields=["name"], limit_page_length=0)])
	bad = 0
	for name in versions:
		v = frappe.get_cached_doc("Crop Protocol Version", name)
		p = ls.params_from_version(name)
		if not cint(p["ms_life_weeks"]):
			continue
		c = ls.build_cycles(p, 1000, "2026-01-05", 1)[0]
		end = c["line_end_sw"]

		over = [st for st in c["stages"] if st["expiry_sw"] > end]
		note = []
		if end != c["first_cut_sw"] + p["ms_life_weeks"]:
			note.append("clearance is not first cut + life")
		if over:
			note.append("%d tranche(s) outlive the block" % len(over))

		for divert in DIVERTS:
			r = ls.simulate(p, 1000, "2026-01-05", num_cycles=1,
			                default_to_prop_pct=divert, horizon_weeks=end + 60)
			live = [x["sw"] for x in r["rows"] if cint(x.get("total_cap") or 0)]
			if live and live[-1] >= end:
				note.append("still cutting in week %d at %d%% divert (clears %d)"
				            % (live[-1], divert, end))
			# Deliberately no ceiling on the peak pool. A pool compounding hard
			# WITHIN the line is arithmetic, not a fault -- what was wrong before
			# was compounding PAST it, which the week check above is what catches.

		if note:
			bad += 1
			print("  FAIL %-34s %s" % (name[:34], "; ".join(note)))
		else:
			print("  ok   %-34s clears wk %-4s cutting weeks per tranche %s"
			      % (name[:34], end,
			         "/".join(str(st.get("cutting_weeks")) for st in c["stages"])))

	print("\n%d of %d protocol versions fail the line-clear rule"
	      % (bad, len(versions)))
	return bad
