"""25,000 plants at one cycle should be 25,000 TC. Prove it, then vary one thing at a time."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()


def _run():
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.crop_protocol import set_route_quantities

	v = frappe.get_doc("Crop Protocol Version",
	                   frappe.get_all("Crop Protocol Version",
	                                  filters={"version_status": "Active"},
	                                  pluck="name", limit=1)[0])

	def setup(cycles=1, per_cycle=1.0, per_week=1.0, cpr=1.0, reject=0.0,
	          order_loss=0.0, tray=3, pot=8, life=52):
		v.max_multiplication_cycles = cycles
		v.multiplication_factor_per_cycle = per_cycle
		v.cuttings_per_plant_per_week = per_week
		v.cuttings_per_plant_required = cpr
		v.cutting_reject_pct = reject
		v.tc_order_loss_pct = order_loss
		v.weeks_on_tray, v.weeks_on_pot = tray, pot
		v.motherstock_life_weeks = life
		v.set("material_route", [])
		for st in ("TC", "Motherstock", "Plants"):
			v.append("material_route", {"row_type": "Stage", "stage": st})
		set_route_quantities(v, native=True)

	def ask(plants):
		return sourcing.requirement(v, "TC", plants * 10, plants)

	print("=== the plain case: no losses, 1 cutting per mother per week ===")
	for cycles in (1, 2, 3, 4):
		setup(cycles=cycles)
		r = ask(25000)
		print("   %d cycle(s): pool %s mothers -> TC to buy %s   (25,000 / %d = %s)"
		      % (cycles, f"{r['pool']:,}", f"{r['units']:,}", cycles,
		         f"{25000 // cycles:,}"))

	print("\n=== one thing at a time, all at 1 cycle, 25,000 plants ===")
	base = dict(cycles=1)
	rows = [
		("baseline", {}),
		("rooting+establishment 1.17 cuttings per plant", {"cpr": 1.17}),
		("cutting reject 14%", {"reject": 14.0}),
		("TC order loss 10%", {"order_loss": 10.0}),
		("2 cuttings per mother per week", {"per_week": 2.0}),
		("all of the above", {"cpr": 1.17, "reject": 14.0, "order_loss": 10.0}),
	]
	for label, over in rows:
		setup(**{**base, **over})
		r = ask(25000)
		print("   %-46s cuttings %-9s pool %-9s TC %s"
		      % (label, f"{r['weekly_draw']:,}", f"{r['pool']:,}", f"{r['units']:,}"))

	print("\n=== the real Aster protocol, 25,000 plants in the peak week ===")
	a = frappe.get_doc("Crop Protocol Version", "Aster Pink Flash-Karen-v17")
	r = sourcing.requirement(a, "TC", 250000, 25000)
	print("   %s" % r["basis"])
	print("   cycles %s -> TC %s" % (r["cycles"], f"{r['units']:,}"))
