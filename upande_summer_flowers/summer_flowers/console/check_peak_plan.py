"""Build a procurement plan sized off the peak week. Rolled back."""

import frappe
from frappe.utils import cint, flt


def main(plan="SFPP-2026-00060"):
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import build

	for cycles in (None, 0, 2):
		frappe.db.rollback()
		try:
			name = build(plan, replace=1, cycles=cycles)
		except Exception as e:
			print("cycles=%-4s THROWN %s" % (cycles, str(e)[:110]))
			continue
		d = frappe.get_doc("Summer Flower Procurement Plan", name)
		est = [r for r in d.requirements if r.line_type == "Establishment"]
		print("\ncycles=%s -> %s" % (cycles, name))
		print("   peak week          %s, %s cuttings"
		      % (d.peak_week, f"{cint(d.peak_cuttings):,}"))
		print("   multiplications    %s   (suggested %s)"
		      % (d.multiplication_cycles, d.cycles_suggested))
		print("   generations        %s" % d.generations)
		print("   plantlets to buy   %s" % f"{cint(est[0].qty_to_order):,}")
		print("   mother plants      %s" % f"{cint(d.pool_plants):,}")
		print("   order by           %s" % est[0].order_by_date)
		print("   needed on site     %s" % est[0].required_at_site_date)
		print("   cutting weeks left %s" % d.cutting_weeks_left)
		print("   basis: %s" % (d.sizing_basis or "")[:300])
	frappe.db.rollback()
	print("\n  (rolled back -- nothing kept)")
