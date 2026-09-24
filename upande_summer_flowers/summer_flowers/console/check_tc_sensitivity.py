"""Move one protocol input at a time; see what the TC order does. Rolls back."""

import frappe
from frappe.utils import cint, flt


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.crop_protocol import set_route_quantities

	V = "Aster Pink Flash-Karen-v17"
	PEAK, SEASON = 37000, 550000

	def ask(**over):
		v = frappe.get_doc("Crop Protocol Version", V)
		for k, val in over.items():
			v.set(k, val)
		set_route_quantities(v, native=True)
		r = sourcing.requirement(v, "TC", SEASON, PEAK)
		life, est = cint(v.motherstock_life_weeks), cint(v.weeks_tc_to_first_cut())
		cyc = cint(v.max_multiplication_cycles)
		return (cint(r["weekly_draw"]), cint(r["pool"]), cint(r["units"]),
		        max(0, life - est * max(0, cyc - 1)))

	base = ask()
	print("baseline: %s cuttings -> %s mothers -> %s TC, %s cutting weeks\n"
	      % (f"{base[0]:,}", f"{base[1]:,}", f"{base[2]:,}", base[3]))

	print("%-52s %-10s %-10s %-10s %s"
	      % ("change one thing", "cuttings", "mothers", "TC", "cut wks"))
	print("%-52s %-10s %-10s %-10s %s"
	      % ("(baseline)", f"{base[0]:,}", f"{base[1]:,}", f"{base[2]:,}", base[3]))

	cases = [
		("cuttings per mother per week 1.0 -> 2.0",
		 {"cuttings_per_plant_per_week": 2.0}),
		("cuttings per mother per week 1.0 -> 0.5",
		 {"cuttings_per_plant_per_week": 0.5}),
		("rooting 90% -> 95% (fewer cuttings per plant)",
		 {"cuttings_per_plant_required": 1.0 / (0.95 * 0.95)}),
		("cutting reject 14% -> 5%", {"cutting_reject_pct": 5.0}),
		("cutting reject 14% -> 25%", {"cutting_reject_pct": 25.0}),
		("TC order loss 10% -> 0%", {"tc_order_loss_pct": 0.0}),
		("TC order loss 10% -> 20%", {"tc_order_loss_pct": 20.0}),
		("cycles 4 -> 3", {"max_multiplication_cycles": 3}),
		("cycles 4 -> 5", {"max_multiplication_cycles": 5}),
		("factor per cycle 1.0 -> 1.5", {"multiplication_factor_per_cycle": 1.5}),
		("tray 3 -> 4 weeks (longer establishment)", {"weeks_on_tray": 4}),
		("line life 52 -> 104 weeks", {"motherstock_life_weeks": 104}),
	]
	for label, over in cases:
		c, m, t, w = ask(**over)
		mark = lambda new, old: ("=" if new == old else ("up" if new > old else "down"))
		print("%-52s %-10s %-10s %-10s %s"
		      % (label, "%s %s" % (f"{c:,}", mark(c, base[0])),
		         "%s %s" % (f"{m:,}", mark(m, base[1])),
		         "%s %s" % (f"{t:,}", mark(t, base[2])),
		         "%s %s" % (w, mark(w, base[3]))))

	print("\n  a 1% change in the reject rate:")
	for r in (13.0, 14.0, 15.0):
		c, m, t, w = ask(cutting_reject_pct=r)
		print("     reject %4.1f%% -> %s cuttings, %s TC" % (r, f"{c:,}", f"{t:,}"))
