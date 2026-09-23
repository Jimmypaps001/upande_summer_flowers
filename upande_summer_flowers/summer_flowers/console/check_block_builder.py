"""Draw a block over beds, then plant it and watch the harvest dates move. Rolls back."""

import frappe
from frappe.utils import add_days, cint, getdate


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers import block as blk

	# a greenhouse whose beds are not all claimed
	gh = frappe.db.sql("""
		select greenhouse, count(*) n
		from `tabBed`
		where ifnull(custom_block, '') = '' and ifnull(greenhouse, '') != ''
		  and bed_length > 0 and bed_width > 0
		group by greenhouse having n >= 4 order by n desc limit 1
	""", as_dict=True)
	if not gh:
		print("no greenhouse with free beds on this site")
		return
	g = gh[0].greenhouse
	look = blk.beds_for_range(g)
	print("greenhouse %s" % g)
	print("   %s beds, %s free, %s ha measured, %s unmeasured, %s taken"
	      % (look["beds"], look["free"], look["measured_ha"], look["unmeasured"],
	         len(look["taken"])))
	print("   free run %s-%s" % (look["first_free"], look["last_free"]))

	lo = look["first_free"]
	hi = min(look["last_free"], lo + 3)
	print("\ndrawing a block over beds %s-%s" % (lo, hi))
	out = blk.create_from_beds(greenhouse=g, block="SFTEST", first=lo, last=hi,
	                           summer_flowers=1)
	print("   created %s" % out["block"])
	b = frappe.get_doc("Block", out["block"])
	print("   greenhouse   %s" % b.greenhouse)
	print("   farm         %s" % b.farm)
	print("   summer       %s" % b.custom_is_summer_flower_block)
	print("   beds in table %s   measured %s ha"
	      % (len(b.custom_beds), b.custom_measured_net_area_ha))
	print("   capacity     %s" % (b.get("custom_sf_capacity_note") or "-"))

	# a second block over the same beds must be refused
	try:
		blk.create_from_beds(greenhouse=g, block="SFTEST2", first=lo, last=hi)
		print("\n   !! a second block over the same beds was ALLOWED")
	except frappe.ValidationError as e:
		print("\n   second block over the same beds refused: %s"
		      % str(e).split(".")[0][:100])
