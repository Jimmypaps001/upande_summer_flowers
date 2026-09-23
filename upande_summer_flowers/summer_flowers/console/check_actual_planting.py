"""Record a real planting date; watch the harvest dates follow it. Rolls back."""

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
	name = frappe.db.get_value("Planting Calendar",
	                           {"calendar_status": ["in", ("Approved", "Planted",
	                                                       "Draft")]},
	                           "name")
	if not name:
		print("no planting calendar on this site")
		return
	c = frappe.get_doc("Planting Calendar", name)

	def show(tag):
		print("\n%s" % tag)
		print("   planned  %s   actual %s   counted from %s   slip %s days"
		      % (c.planting_date, c.actual_planting_date or "—",
		         c.harvest_dates_from, c.planting_slip_days))
		print("   pinch %s   uproot %s" % (c.pinch_date, c.planned_uproot_date))
		for r in c.flush_projection[:4]:
			print("      flush %s  %s  %s-W%02d  %s stems"
			      % (r.flush_number, r.harvest_date, r.year, r.week_no,
			         f"{cint(r.expected_stems):,}"))

	c.flags.ignore_permissions = True
	c.save()
	show("as planned (%s, %s)" % (c.name, c.variety))
	first_before = c.flush_projection[0].harvest_date if c.flush_projection else None

	c.actual_planting_date = add_days(getdate(c.planting_date), 17)
	c.save()
	show("planted 17 days late")
	first_after = c.flush_projection[0].harvest_date if c.flush_projection else None
	if first_before and first_after:
		moved = (getdate(first_after) - getdate(first_before)).days
		print("\n   first harvest moved %s days (planting moved 17)" % moved)
		print("   status is now: %s" % c.calendar_status)
