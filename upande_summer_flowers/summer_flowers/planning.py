# Copyright (c) 2026, James Kiruga and contributors
"""ISO-week arithmetic shared by demand, plans and motherstock scheduling."""

import datetime
import re

import frappe
from frappe import _

MONTH_NAMES = [
	"January", "February", "March", "April", "May", "June",
	"July", "August", "September", "October", "November", "December",
]


# The week a financial year opens on. July to June, and the register numbers it by
SEASON_FIRST_WEEK = 27


def season_first_week():
	from upande_summer_flowers.summer_flowers.settings import season_first_week as _w

	return _w(SEASON_FIRST_WEEK)


def season_year(value, default=0):
	"""The year a season starts in, however the site writes it down."""
	if value in (None, ""):
		return default
	if isinstance(value, int):
		return value
	m = re.match(r"\s*(\d{4})", str(value))
	return int(m.group(1)) if m else default


def iso_monday(year, week):
	"""Date of the Monday starting ISO `week` of ISO `year`."""
	return datetime.date.fromisocalendar(int(year), int(week), 1)

def iso_year_week(date):
	"""(iso_year, iso_week) for a date, string or datetime."""
	if isinstance(date, str):
		date = frappe.utils.getdate(date)
	if isinstance(date, datetime.datetime):
		date = date.date()
	cal = date.isocalendar()
	return cal[0], cal[1]

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


def week_family_label(weeks, limit=140):
	"""The weeks of the year a planting is cut in, written so a person can read it."""
	nums = sorted({int(w) for w in weeks if w})
	if not nums:
		return None
	runs, start, prev = [], nums[0], nums[0]
	for w in nums[1:]:
		if w == prev + 1:
			prev = w
			continue
		runs.append((start, prev))
		start = prev = w
	runs.append((start, prev))
	label = ", ".join(f"wk{a}" if a == b else f"wk{a}-wk{b}" for a, b in runs)
	if len(label) <= limit:
		return label
	# Still too long: say how many weeks rather than lose the meaning in a truncation.
	return f"{len(nums)} weeks, wk{nums[0]}-wk{nums[-1]}"[:limit]
