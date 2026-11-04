# Copyright (c) 2026, James Kiruga and contributors
"""A stem length grade."""

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
		if not self.length_cm:
			found = re.search(r"\d+", self.grade_name)
			if found:
				self.length_cm = int(found.group())
