# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""ISO-week arithmetic shared by demand, plans and motherstock scheduling.

Everything in Summer Flowers is planned on ISO weeks starting Monday. ISO years
carry either 52 or 53 weeks, so week arithmetic must never assume 52 -- a flush
family drifts back one calendar week each time it crosses a 53-week year.
"""

import datetime

import frappe
from frappe import _

MONTH_NAMES = [
	"January", "February", "March", "April", "May", "June",
	"July", "August", "September", "October", "November", "December",
]


# The week a financial year opens on. July to June, and the register numbers it by
# ISO week rather than by date: W27 onward belongs to the year the season starts in,
# W1-W26 to the next. Everything that has to agree about which season a week is in
# reads this, because deriving it twice is how a plan came to open a week after the
# register it was built from.
SEASON_FIRST_WEEK = 27


def iso_monday(year, week):
	"""Date of the Monday starting ISO `week` of ISO `year`."""
	return datetime.date.fromisocalendar(int(year), int(week), 1)


def weeks_in_iso_year(year):
	"""53 if this ISO year has a week 53, else 52."""
	year = int(year)
	try:
		datetime.date.fromisocalendar(year, 53, 1)
		return 53
	except ValueError:
		return 52


def iso_year_week(date):
	"""(iso_year, iso_week) for a date, string or datetime."""
	if isinstance(date, str):
		date = frappe.utils.getdate(date)
	if isinstance(date, datetime.datetime):
		date = date.date()
	cal = date.isocalendar()
	return cal[0], cal[1]


def add_weeks(year, week, delta):
	"""Move `delta` ISO weeks from (year, week), honouring 53-week years."""
	return iso_year_week(iso_monday(year, week) + datetime.timedelta(weeks=int(delta)))


def week_sequence(start_year, start_week, count):
	"""[(year, week, monday_date), ...] of `count` consecutive ISO weeks."""
	start = iso_monday(start_year, start_week)
	out = []
	for i in range(int(count)):
		d = start + datetime.timedelta(weeks=i)
		y, w = iso_year_week(d)
		out.append((y, w, d))
	return out


def weeks_between(from_year, from_week, to_year, to_week):
	"""Whole ISO weeks from one week to another. Negative if `to` precedes `from`."""
	delta = iso_monday(to_year, to_week) - iso_monday(from_year, from_week)
	return delta.days // 7


def current_week():
	"""(iso_year, iso_week) for today."""
	return iso_year_week(frappe.utils.nowdate())


def month_of(year, week):
	"""(month_number, month_name) the Monday of this ISO week falls in."""
	d = iso_monday(year, week)
	return d.month, MONTH_NAMES[d.month - 1]


def validate_week(week, label=None):
	if not week or int(week) < 1 or int(week) > 53:
		frappe.throw(_("{0} must be an ISO week between 1 and 53.").format(label or _("Week")))
