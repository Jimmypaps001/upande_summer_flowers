"""Create a Motherstock Plan, alter the knobs, and roll it back."""

import frappe
from frappe.utils import cint


def main(plan=None):
	from upande_summer_flowers.summer_flowers import propagation_solver as ps
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_motherstock_plan.summer_flower_motherstock_plan import for_plan

	if not plan:
		for row in frappe.get_all("Summer Flower Production Plan",
		                          filters={"docstatus": ["<", 2]}, fields=["name"],
		                          order_by="modified desc", limit_page_length=40):
			d = frappe.get_doc("Summer Flower Production Plan", row.name)
			if d.get("protocol") and ps.recommend(d).get("solvable"):
				plan = row.name
				break
	print("production plan: %s" % plan)

	name = for_plan(plan)
	doc = frappe.get_doc("Summer Flower Motherstock Plan", name)
	def show(d, label):
		print("\n  %s  %s" % (label, d.name))
		print("    order %s plantlets by %s, divert %s weeks"
		      % (f"{d.tc_to_order:,}", d.order_by_date, d.divert_weeks))
		print("    generations=%s  peak pool=%s  to field=%s  covers=%s (%s/%s weeks)"
		      % (d.generations, f"{d.peak_pool:,}", f"{d.plants_to_field:,}",
		         d.covers, d.weeks_met, d.weeks_required))
		print("    schedule rows=%s  generation rows=%s  cleared %s"
		      % (len(d.schedule), len(d.generation_table), d.line_end_date))
		for g in d.generation_table:
			print("      gen %s: %s mothers from %s arrivals, cuts from %s for %s weeks"
			      % (g.generation, f"{g.mothers:,}", g.arrivals, g.first_cut_date,
			         g.cutting_weeks))
	show(doc, "created")

	# the playground: move a knob, rebuild, see it change
	for d_weeks in (10, 20):
		doc.divert_weeks = d_weeks
		doc.tc_to_order = 0
		doc.rebuild()
		doc.save()
		show(frappe.get_doc("Summer Flower Motherstock Plan", doc.name),
		     "after divert=%d" % d_weeks)

	print("\n  one per production plan? second call returns: %s"
	      % for_plan(plan))
	frappe.db.rollback()
	print("\n  (rolled back -- nothing kept)")
