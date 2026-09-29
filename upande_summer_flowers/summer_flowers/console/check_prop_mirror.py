"""Does the propagation plan now agree with the procurement plan? Rolled back."""

import frappe
from frappe.utils import cint


def main(plan="SFPP-2026-00061"):
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_propagation_plan.summer_flower_propagation_plan import (
			build_from_plan,
		)

	proc = frappe.get_all("Summer Flower Procurement Plan",
	                      filters={"production_plan": plan, "docstatus": 1},
	                      fields=["name", "motherstock_plan"], limit_page_length=1)
	units = None
	if proc:
		est = frappe.get_all("Planting Material Requirement",
		                     filters={"parent": proc[0].name,
		                              "line_type": "Establishment"},
		                     fields=["qty_to_order", "order_by_date"])
		units = cint(est[0].qty_to_order) if est else None
		print("procurement plan %s: buy %s, order by %s"
		      % (proc[0].name, f"{units:,}" if units else "-",
		         est[0].order_by_date if est else "-"))

	name = build_from_plan(plan)
	d = frappe.get_doc("Summer Flower Propagation Plan",
	                   name if isinstance(name, str) else name.get("name"))
	print("propagation plan %s (rebuilt)" % d.name)
	print("   tc_plants_required   %s" % f"{cint(d.tc_plants_required):,}")
	print("   tc_order_date        %s" % d.tc_order_date)
	print("   mother_plants        %s" % f"{cint(d.mother_plants_required):,}")
	print("   cuttings required    %s" % f"{cint(d.total_cuttings_required):,}")
	print("   from new motherstock %s" % f"{cint(d.cuttings_from_new):,}")
	print("   uncovered            %s" % f"{cint(d.cuttings_uncovered):,}")
	print("   cutting weeks (gen1) %s   block cleared %s"
	      % (d.new_pool_cutting_weeks, d.new_pool_expiry))
	print("   sources:")
	for srow in d.sources:
		print("      %-32s %s mothers, %s -> %s"
		      % (srow.source_type, f"{cint(srow.mother_plants):,}",
		         srow.available_from, srow.available_to))
	short = [w for w in d.weeks if cint(w.shortfall)]
	print("   weeks short: %d of %d" % (len(short), len(d.weeks)))
	ms = frappe.db.get_value("Summer Flower Motherstock Plan",
	                         {"production_plan": plan}, "name")
	sched = {}
	if ms:
		for w in frappe.get_doc("Summer Flower Motherstock Plan", ms).schedule:
			sched[str(w.week_start)] = w
	for w in short:
		m = sched.get(str(w.week_start_date))
		print("      %s needs %s, got %s, short %s  | motherstock row: %s"
		      % (w.week_start_date, f"{cint(w.cuttings_required):,}",
		         f"{cint(w.from_new_ms):,}", f"{cint(w.shortfall):,}",
		         ("cut %s, to mult %s, to field %s"
		          % (cint(m.cuttings_cut), cint(m.to_multiplication),
		             cint(m.to_field))) if m else "NONE -- outside the line"))

	if units is not None:
		same = cint(d.tc_plants_required) == units
		print("\n   MIRRORS THE PROCUREMENT PLAN: %s  (%s vs %s)"
		      % ("yes" if same else "NO", f"{cint(d.tc_plants_required):,}",
		         f"{units:,}"))
	frappe.db.rollback()
	print("\n   (rolled back -- nothing kept)")
