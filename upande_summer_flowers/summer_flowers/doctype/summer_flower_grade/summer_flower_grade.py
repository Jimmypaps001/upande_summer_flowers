# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""A stem length grade.

Grades were free text in two places -- the demand register's split and the
protocol's grade mix -- and they drifted apart exactly as free text does: this
site holds "50" on one protocol and "50cm" on six others, which read as two
grades to anything that groups by them. One record per grade, linked from both,
is the only way the two tables can be compared at all.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document


class SummerFlowerGrade(Document):
	def validate(self):
		self.grade_name = (self.grade_name or "").strip()
		if not self.grade_name:
			frappe.throw(_("Name the grade."))
		# The length is what everything sorts and compares by, so it is read off the
		# name when it was not given -- "60cm" is a 60. A grade whose name carries no
		# number keeps whatever was typed.
		if not self.length_cm:
			found = re.search(r"\d+", self.grade_name)
			if found:
				self.length_cm = int(found.group())
