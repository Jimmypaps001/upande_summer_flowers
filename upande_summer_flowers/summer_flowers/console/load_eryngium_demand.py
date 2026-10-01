"""Kariki's Eryngium market demand, monthly in the workbook, weekly here.

Source: Eryngium Production Plannning, "Market Demand" sheet. It holds stems per
month per variety for three supplying groups -- Kariki, Marginpar Ethiopia and
KE & ET. Only the Kariki rows are loaded.

Two judgement calls, both stated rather than buried:

  Months to weeks.  A month's stems are spread evenly across the ISO weeks whose
  Monday falls in that month, so a 4-week month gives four equal weeks and a
  5-week month five. The workbook has no weekly shape to preserve, and inventing
  one would be inventing seasonality the farm has not stated.

  Which farm.  The sheet says "Kariki", which is a supplying group, not a farm.
  Each variety is put on the farm the 2026 Farms Order says grows it, and where
  it names two the demand is split in the same proportion. That puts Scorpius and
  most of Magnetar on Carzan MR, because that is where the order grows them.

    bench --site <site> execute ...load_eryngium_demand.main
    bench --site <site> execute ...load_eryngium_demand.main --kwargs '{"apply":1}'
"""

import datetime

import frappe
from frappe.utils import cint, flt, getdate

BOOK = "/home/james/Downloads/Eryngium Production Plannning (2).xlsx"
REGION = "Kariki"
FROM_YEAR = 2026

# the workbook's short name -> the Item on this site
VARIETY = {
	"Supernova": "Eryngium Supernova Questar",
	"Magnetar": "Eryngium Magnetar Questar",
	"Aquarius": "Eryngium Aquarius Questar",
	"Orion": "Eryngium Orion Questar",
	"Sirius": "Eryngium Sirius Questar",
	"Scorpius": "Eryngium Scorpius Questar",
	"Germini": "Eryngium planum Gemini Questar",
	"Artemis": "Artemis",
}

# where the 2026 Farms Order grows each one, and in what proportion
GROWN_AT = {
	"Supernova": {"Kariki Molo": 600000},
	"Magnetar": {"Kariki Molo": 150000, "Carzan MR": 343900},
	"Aquarius": {"Kariki Naivasha": 403200},
	"Orion": {"Kariki Nanyuki": 330000},
	"Sirius": {"Kariki Naivasha": 460800},
	"Scorpius": {"Carzan MR": 157000},
	"Artemis": {"Kariki Naivasha": 201600},
	"Germini": {"Kariki Naivasha": 201600},
}


def _weeks_of(month_start):
	"""Every ISO week whose Monday falls in this month."""
	d = getdate(month_start)
	first = d - datetime.timedelta(days=d.weekday())
	if first < d:
		first += datetime.timedelta(days=7)
	out, cur = [], first
	while cur.month == d.month and cur.year == d.year:
		out.append(cur)
		cur += datetime.timedelta(days=7)
	return out


def read_kariki(from_year=FROM_YEAR):
	import openpyxl

	wb = openpyxl.load_workbook(BOOK, read_only=True, data_only=True)
	ws = wb["Market Demand"]
	rows = list(ws.iter_rows(values_only=True))
	hdr = [str(c) if c else "" for c in rows[0]]
	out = {}
	for r in rows[1:]:
		if not r or str(r[0]).strip() != REGION:
			continue
		when = r[1]
		if not isinstance(when, datetime.datetime) or when.year < cint(from_year):
			continue
		for j, h in enumerate(hdr):
			if j < 2 or h not in VARIETY:
				continue
			try:
				stems = float(r[j] or 0)
			except Exception:
				stems = 0
			if stems <= 0:
				continue
			out.setdefault(h, {})[when.date()] = (
				out.setdefault(h, {}).get(when.date(), 0) + stems)
	wb.close()
	return out


def main(apply=0, from_year=FROM_YEAR):
	apply = cint(apply)
	monthly = read_kariki(from_year)
	if not monthly:
		print("nothing to load")
		return

	company = frappe.db.get_value("Farm", "Kariki Naivasha", "company") \
		or frappe.defaults.get_global_default("company")
	print("company %s, from %s, region %s\n" % (company, from_year, REGION))

	plan = []
	for short, by_month in sorted(monthly.items()):
		item = VARIETY[short]
		farms = GROWN_AT[short]
		total_share = sum(farms.values())
		for farm, share in farms.items():
			weeks = {}
			for month, stems in by_month.items():
				ws_ = _weeks_of(month)
				if not ws_:
					continue
				per = stems * (share / total_share) / len(ws_)
				for w in ws_:
					weeks[w] = weeks.get(w, 0) + per
			plan.append((item, farm, short, share / total_share, weeks))

	print("%-34s %-17s %6s %9s %12s" % ("variety", "farm", "share", "weeks", "stems"))
	for item, farm, short, share, weeks in plan:
		print("%-34s %-17s %5.0f%% %9d %12s"
		      % (item[:34], farm, share * 100, len(weeks),
		         format(int(sum(weeks.values())), ",")))
	print("\n%d demand records, %s stems in all"
	      % (len(plan), format(int(sum(sum(w.values()) for *_x, w in plan)), ",")))

	if not apply:
		print("\n   (dry run -- pass apply=1)")
		return

	made = 0
	for item, farm, short, share, weeks in plan:
		name = "%s-%s" % (item, farm)
		doc = (frappe.get_doc("Summer Flower Market Demand", name)
		       if frappe.db.exists("Summer Flower Market Demand", name)
		       else frappe.new_doc("Summer Flower Market Demand"))
		doc.variety, doc.farm = item, farm
		doc.company = doc.company or company
		doc.target_years_ahead = doc.target_years_ahead or 3
		doc.set("demand_weeks", [])
		for monday in sorted(weeks):
			iso = monday.isocalendar()
			doc.append("demand_weeks", {
				"year": iso[0], "week_no": iso[1],
				"week_start_date": monday,
				"demand_stems": int(round(weeks[monday])),
			})
		doc.flags.ignore_permissions = True
		doc.save()
		made += 1
	frappe.db.commit()
	print("\n   %d market demand records written" % made)
