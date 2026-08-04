# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""A production budget for one block over one period.

Costs are held as lines under a category so the same document answers both "what
will this block cost" and "where is it running over". Actuals are read live from
the ledger against the budget's cost centre rather than keyed in, so the variance
cannot drift away from the accounts.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime

CATEGORIES = ("Labour", "Chemicals", "Fertilisers", "Inputs", "Other")
APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")


def _has_role(roles=APPROVER_ROLES):
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


class BlockProductionBudget(Document):
	def validate(self):
		self.check_period()
		self.pull_context()
		self.set_line_amounts()
		self.load_actuals()
		self.roll_up()

	def check_period(self):
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("The period ends before it starts."))
		self.period_weeks = max(
			1, ((getdate(self.to_date) - getdate(self.from_date)).days + 1) // 7)
		if not self.fiscal_year:
			self.fiscal_year = frappe.db.get_value(
				"Fiscal Year",
				{"year_start_date": ["<=", self.from_date],
				 "year_end_date": [">=", self.from_date]}, "name")
		# One live budget per block per period, or the same actuals get counted twice.
		clash = frappe.db.sql("""
			select name from `tabBlock Production Budget`
			where block = %(block)s and name != %(name)s
			  and status != 'Rejected' and docstatus < 2
			  and from_date <= %(to_date)s and to_date >= %(from_date)s
			limit 1
		""", {"block": self.block, "name": self.name or "",
		      "from_date": self.from_date, "to_date": self.to_date})
		if clash:
			frappe.throw(_(
				"Budget {0} already covers block {1} between {2} and {3}. "
				"Overlapping budgets would count the same actuals twice."
			).format(clash[0][0], self.block, self.from_date, self.to_date))

	def pull_context(self):
		if not self.currency:
			self.currency = frappe.db.get_value("Company", self.company,
			                                    "default_currency")
		if self.crop_cycle:
			cyc = frappe.db.get_value(
				"Crop Cycle", self.crop_cycle,
				["custom_block", "custom_sf_variety", "custom_area_planted_sqm"],
				as_dict=True)
			if cyc:
				self.block = self.block or cyc.custom_block
				self.variety = self.variety or cyc.custom_sf_variety
				if flt(cyc.custom_area_planted_sqm):
					self.area_planted_sqm = flt(cyc.custom_area_planted_sqm)
		if self.block and not self.farm:
			self.farm = frappe.db.get_value("Block", self.block, "farm")
		if not flt(self.area_planted_sqm) and self.block:
			self.area_planted_sqm = flt(frappe.db.get_value(
				"Block", self.block, "custom_net_area_ha")) * 10_000

	def set_line_amounts(self):
		for r in self.budget_lines:
			r.currency = self.currency
			r.amount = flt(r.qty) * flt(r.rate)
			if not r.cost_center:
				r.cost_center = self.cost_center
			if r.item:
				if not r.description:
					r.description = frappe.db.get_value("Item", r.item, "item_name")
				if not r.uom:
					r.uom = frappe.db.get_value("Item", r.item, "stock_uom")

	def load_actuals(self):
		"""Actuals per category from posted GL entries on this cost centre.

		The mapping is by expense account, because a line's account is the only
		thing that ties the plan to the ledger. A category whose lines carry no
		account therefore reports zero actual rather than silently absorbing
		everything else posted to the cost centre.
		"""
		self._actual_by_category = {c: 0.0 for c in CATEGORIES}
		if not self.cost_center:
			return
		accounts = {}
		for r in self.budget_lines:
			if r.expense_account:
				accounts.setdefault(r.cost_category, set()).add(r.expense_account)
		if not accounts:
			return
		all_accounts = sorted({a for s in accounts.values() for a in s})
		rows = frappe.db.sql("""
			select account, sum(debit) - sum(credit) as amt
			from `tabGL Entry`
			where cost_center = %(cc)s and is_cancelled = 0
			  and posting_date between %(from_date)s and %(to_date)s
			  and account in %(accounts)s
			group by account
		""", {"cc": self.cost_center, "from_date": self.from_date,
		      "to_date": self.to_date, "accounts": all_accounts}, as_dict=True)
		by_account = {r.account: flt(r.amt) for r in rows}
		for category, accs in accounts.items():
			self._actual_by_category[category] = sum(
				by_account.get(a, 0.0) for a in accs)

	def roll_up(self):
		budgeted = {c: 0.0 for c in CATEGORIES}
		for r in self.budget_lines:
			budgeted[r.cost_category] = budgeted.get(r.cost_category, 0.0) + flt(r.amount)
		actual = getattr(self, "_actual_by_category", {})

		self.set("category_totals", [])
		for c in CATEGORIES:
			b, a = flt(budgeted.get(c)), flt(actual.get(c))
			if not b and not a:
				continue
			self.append("category_totals", {
				"cost_category": c, "budgeted_amount": b, "actual_amount": a,
				"variance_amount": b - a,
				"variance_pct": ((b - a) * 100.0 / b) if b else 0,
				"currency": self.currency,
			})

		self.total_budgeted = sum(flt(r.amount) for r in self.budget_lines)
		self.total_actual = sum(flt(v) for v in actual.values())
		self.total_variance = flt(self.total_budgeted) - flt(self.total_actual)
		self.variance_pct = (
			self.total_variance * 100.0 / self.total_budgeted
			if self.total_budgeted else 0)
		self.cost_per_sqm = (
			flt(self.total_budgeted) / flt(self.area_planted_sqm)
			if flt(self.area_planted_sqm) else 0)

	# ------------------------------------------------------------- actions
	@frappe.whitelist()
	def submit_for_approval(self):
		if not self.budget_lines:
			frappe.throw(_("Add at least one budget line."))
		self.status = "Pending Approval"
		self.rejection_reason = None
		self.save()
		return self.status

	@frappe.whitelist()
	def approve(self):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can approve a production budget."))
		if self.status != "Pending Approval":
			frappe.throw(_("Submit the budget for approval first."))
		self.status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_on = now_datetime()
		self.save()
		return self.status

	@frappe.whitelist()
	def reject(self, reason=None):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can reject a production budget."))
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is required."))
		self.status = "Rejected"
		self.rejection_reason = reason
		self.save()
		return self.status

	@frappe.whitelist()
	def refresh_actuals(self):
		"""Re-read the ledger without changing anything else."""
		self.save()
		return {"actual": self.total_actual, "variance": self.total_variance,
		        "variance_pct": self.variance_pct}
