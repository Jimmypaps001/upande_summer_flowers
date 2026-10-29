# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""A material stage and what it means to the planner. See stages.py."""

from frappe.model.document import Document


class MaterialStage(Document):
	def on_update(self):
		from upande_summer_flowers.summer_flowers import stages

		stages.clear_cache()

	def on_trash(self):
		from upande_summer_flowers.summer_flowers import stages

		stages.clear_cache()
