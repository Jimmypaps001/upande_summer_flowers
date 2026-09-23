"""What the block picker will show, rendered as text."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())


def _run():
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			allocation_options,
		)

	best = None
	for p in frappe.get_all("Summer Flower Production Plan",
	                        filters={"docstatus": ["<", 2]},
	                        fields=["name", "variety", "farm", "protocol"],
	                        order_by="modified desc", limit=40):
		if not p.protocol:
			continue
		try:
			s = allocation_options(p.name)
		except Exception:
			continue
		cands = sum(len(r["candidates"]) for r in s["rows"])
		if s["rows"] and cands and (best is None or cands > best[1]):
			best = (s, cands)
		if best and best[1] > 10:
			break
	if not best:
		print("no plan with allocatable rows")
		return
	s = best[0]
	print("Allocate blocks — %s at %s" % (s["variety"], s["farm"]))
	placed = sum(1 for r in s["rows"] if r["block"] or r["suggested"])
	print("%d of %d placed%s\n" % (placed, len(s["rows"]),
	      "" if placed == len(s["rows"])
	      else "  ·  %d without a block" % (len(s["rows"]) - placed)))
	for r in s["rows"][:6]:
		chosen = r["block"] or r["suggested"] or ""
		print("  %s   %s · %s beds · %s plants   -> %s"
		      % (r["planting_week"], r["planting_date"], r["beds"],
		         f"{r['plants']:,}", (chosen.split(" - ")[-1] if chosen
		                              else "NO BLOCK")))
		if not r["candidates"]:
			print("      (no block has %s beds free for that whole period)" % r["beds"])
		for c in r["candidates"]:
			mark = "*" if c["block"] == chosen else " "
			if c["fits"]:
				sub = "%s spare" % (c["free_beds"] - r["beds"])
				kind = "fits"
			elif c["free_from"]:
				sub = "free %s · %sw late" % (c["free_from"], c["weeks_late"])
				kind = "late"
			else:
				sub = "no room"
				kind = "no "
			held = "; ".join("%s holds %s to %s" % (b["planting"], b["beds"],
			                                        b["frees_on"])
			                 for b in (c["blockers"] or [])[:2])
			print("     %s [%-8s %2s/%-3s %-4s] %-26s %s"
			      % (mark, c["block"].split(" - ")[-1][:8], c["free_beds"],
			         c["total_beds"], kind, sub, held[:60]))
		print()
