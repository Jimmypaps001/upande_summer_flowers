"""A cycle occupies one thing, and the right one. Rolls back."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	print("existing cycles by scope:")
	rows = frappe.db.sql("""
		select ifnull(custom_cycle_scope,'(unset)') scope,
		       sum(ifnull(custom_block,'')  != '') with_block,
		       sum(ifnull(greenhouse,'')    != '') with_house,
		       sum(ifnull(custom_block,'')  != "" and ifnull(greenhouse,"") != "") both_set,
		       count(*) n
		from `tabCrop Cycle` group by 1""", as_dict=True)
	for r in rows:
		print("   %-12s n=%-5s block=%-5s greenhouse=%-5s BOTH=%s"
		      % (r.scope, r.n, cint(r.with_block), cint(r.with_house), cint(r.both_set)))

	def attempt(label, **vals):
		d = frappe.new_doc("Crop Cycle")
		d.update(vals)
		d.flags.ignore_permissions = True
		d.flags.ignore_mandatory = True
		try:
			d.insert()
			print("   %-46s ALLOWED  scope=%s" % (label, d.custom_cycle_scope))
		except frappe.ValidationError as e:
			print("   %-46s refused: %s" % (label, str(e).split(".")[0][:74]))
		except Exception as e:
			print("   %-46s %s: %s" % (label, type(e).__name__, str(e)[:60]))

	sf = frappe.db.get_value("Crop Protocol", {"custom_is_summer_flower": 1},
	                         ["variety", "farm"], as_dict=True)
	blk = frappe.db.get_value("Block", {"custom_is_summer_flower_block": 1},
	                          ["name", "greenhouse"], as_dict=True)
	house = frappe.db.get_value("Greenhouse", {}, "greenhouse")
	print("\nusing variety %s, block %s, greenhouse %s"
	      % (sf.variety if sf else "-", blk.name if blk else "-", house))

	print("\ntrying to create cycles:")
	attempt("summer flower + block (right)",
	        variety=sf.variety, farm=sf.farm, custom_block=blk.name)
	attempt("summer flower + greenhouse (wrong thing)",
	        variety=sf.variety, farm=sf.farm, greenhouse=house)
	attempt("summer flower + both",
	        variety=sf.variety, farm=sf.farm, custom_block=blk.name,
	        greenhouse=house)
	attempt("summer flower + neither",
	        variety=sf.variety, farm=sf.farm)
	attempt("block scope forced onto a greenhouse cycle",
	        variety=sf.variety, farm=sf.farm, custom_cycle_scope="Greenhouse",
	        custom_block=blk.name)
