# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import calendar
import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from upande_summer_flowers.summer_flowers.planning import MONTH_NAMES


class SummerFlowerBudget(Document):
	def validate(self):
		self.set_fiscal_years_on_months()
		self.value_months()
		self.roll_up_fiscal_years()

	# ------------------------------------------------------------------ months
	def set_fiscal_years_on_months(self):
		for row in self.budget_months:
			row.month_start = datetime.date(row.year, row.month, 1)
			row.month_end = datetime.date(
				row.year, row.month, calendar.monthrange(row.year, row.month)[1]
			)
			row.month_name = MONTH_NAMES[row.month - 1]
			if not row.fiscal_year:
				row.fiscal_year = fiscal_year_for(row.month_start, self.company)

	def value_months(self):
		rate = flt(self.price_per_stem)
		for row in self.budget_months:
			row.value = flt(row.stems) * rate

		self.total_stems = sum((r.stems or 0) for r in self.budget_months)
		self.total_value = sum(flt(r.value) for r in self.budget_months)
		self.months_covered = len(self.budget_months)

	def roll_up_fiscal_years(self):
		"""Percentages are computed within each fiscal year, which is what a
		Monthly Distribution needs (it must total 100 per year)."""
		by_fy = {}
		for row in self.budget_months:
			fy = by_fy.setdefault(row.fiscal_year, {"stems": 0, "value": 0.0, "months": 0})
			fy["stems"] += row.stems or 0
			fy["value"] += flt(row.value)
			fy["months"] += 1

		for row in self.budget_months:
			total = by_fy.get(row.fiscal_year, {}).get("stems") or 0
			row.pct_of_fiscal_year = ((row.stems or 0) / total * 100) if total else 0

		existing = {r.fiscal_year: r for r in self.fiscal_years}
		self.fiscal_years = []
		for fy in sorted(by_fy, key=lambda f: str(f)):
			prev = existing.get(fy)
			self.append("fiscal_years", {
				"fiscal_year": fy,
				"stems": by_fy[fy]["stems"],
				"value": by_fy[fy]["value"],
				"months_covered": by_fy[fy]["months"],
				# Preserve links already written to the accounts side.
				"monthly_distribution": prev.monthly_distribution if prev else None,
				"erpnext_budget": prev.erpnext_budget if prev else None,
			})

	# --------------------------------------------------------- accounts push
	@frappe.whitelist()
	def post_to_accounts(self):
		"""Write one Monthly Distribution and one Budget per fiscal year.

		This site budgets against Farm (Budget.budget_against has a Farm option and a
		custom `farm` field), and Budget.budget_distribution carries the monthly
		amounts, so the distribution lands directly on the Budget too.
		"""
		if not self.budget_account:
			frappe.throw(_("Set the Income Account before posting to accounts."))
		if not self.total_value:
			frappe.throw(_("Budget has no value to post. Set Price per Stem."))

		# Checked before anything is written, for the same reason the fiscal years
		# are: a site that cannot budget against Farm has to budget against the cost
		# centre, and finding that out half way through leaves Monthly Distributions
		# behind with no Budget to hang them on.
		if self._budget_dimension() == "Cost Center" and not self.cost_center:
			frappe.throw(
				_("This site's Budget cannot be budgeted against Farm, so it has to be "
				  "budgeted against a Cost Center -- and this budget has none. Set the "
				  "Cost Center, then post again."),
				title=_("Cost Center required"),
			)

		# Check every fiscal year up front. Fiscal Years can be restricted to a
		# subset of companies, and failing mid-loop would leave some years posted
		# and the rest not.
		blocked = [
			fy.fiscal_year for fy in self.fiscal_years
			if not fiscal_year_available(fy.fiscal_year, self.company)
		]
		if blocked:
			frappe.throw(
				_("Fiscal Year(s) {0} are not enabled for company {1}. Add the company "
				  "to those Fiscal Years, or shorten the plan horizon, then post again.").format(
					frappe.bold(", ".join(blocked)), frappe.bold(self.company)
				),
				title=_("Fiscal Year not available"),
			)

		created = []
		for fy_row in self.fiscal_years:
			months = [r for r in self.budget_months if r.fiscal_year == fy_row.fiscal_year]
			if not months:
				continue

			md = self._upsert_monthly_distribution(fy_row, months)
			budget = self._upsert_budget(fy_row, months, md)
			fy_row.db_set("monthly_distribution", md)
			fy_row.db_set("erpnext_budget", budget)
			created.append((fy_row.fiscal_year, md, budget))

		self.db_set("budget_status", "Posted to Accounts")
		return [
			{"fiscal_year": fy, "monthly_distribution": md, "budget": b}
			for fy, md, b in created
		]

	def _distribution_id(self, fiscal_year):
		return f"SF {self.variety} {self.farm} {fiscal_year}"[:140]

	def _budget_dimension(self):
		"""What this site's Budget can actually be budgeted against.

		Budget belongs to ERPNext, and budgeting against Farm is a customisation:
		it needs both a `farm` field and Farm among budget_against's options. A site
		without them has only Cost Center and Project, so asking for Farm wrote an
		invalid option and filtering by farm was a SELECT on a column that is not
		there -- which is why posting to accounts did nothing at all on a site that
		had never been customised that way.
		"""
		meta = frappe.get_meta("Budget")
		field = meta.get_field("budget_against")
		options = [o.strip() for o in (field.options or "").split("\n")] if field else []
		if meta.has_field("farm") and "Farm" in options:
			return "Farm"
		return "Cost Center"

	def _find_existing_budget(self, fiscal_year):
		"""Budget already covering this company / dimension / account / fiscal year."""
		filters = {
			"company": self.company,
			"account": self.budget_account,
			"from_fiscal_year": fiscal_year,
			"docstatus": ["<", 2],
		}
		if self._budget_dimension() == "Farm":
			filters["farm"] = self.farm
		elif self.cost_center:
			filters["cost_center"] = self.cost_center
		rows = frappe.get_all("Budget", filters=filters, pluck="name", limit=1)
		return rows[0] if rows else None

	@staticmethod
	def _balanced_percents(months):
		"""[(row, percent)] summing to exactly 100.

		Both Monthly Distribution and Budget validate that percentages total 100, so
		the rounding remainder is pushed onto the largest month instead of drifting.
		"""
		pcts = [(r, flt(r.pct_of_fiscal_year, 3)) for r in months]
		drift = round(100 - sum(p for _r, p in pcts), 3)
		if pcts and drift:
			biggest = max(range(len(pcts)), key=lambda i: pcts[i][1])
			pcts[biggest] = (pcts[biggest][0], round(pcts[biggest][1] + drift, 3))
		return pcts

	def _upsert_monthly_distribution(self, fy_row, months):
		name = self._distribution_id(fy_row.fiscal_year)
		pcts = self._balanced_percents(months)

		if frappe.db.exists("Monthly Distribution", name):
			doc = frappe.get_doc("Monthly Distribution", name)
			doc.percentages = []
		else:
			doc = frappe.new_doc("Monthly Distribution")
			doc.distribution_id = name

		doc.fiscal_year = fy_row.fiscal_year
		for row, pct in pcts:
			doc.append("percentages", {
				"month": MONTH_NAMES[row.month - 1],
				"percentage_allocation": pct,
			})
		doc.save(ignore_permissions=True)
		return doc.name

	def _upsert_budget(self, fy_row, months, monthly_distribution):
		# ERPNext rejects a second Budget for the same farm + account + overlapping
		# fiscal year, so adopt whatever is already there rather than colliding.
		existing = fy_row.erpnext_budget or self._find_existing_budget(fy_row.fiscal_year)

		if existing and frappe.db.exists("Budget", existing):
			doc = frappe.get_doc("Budget", existing)
			if doc.docstatus != 0:
				frappe.throw(
					_("Budget {0} for fiscal year {1} is already submitted, so this plan "
					  "cannot rewrite it. Cancel or amend it in Accounts first.").format(
						frappe.bold(doc.name), frappe.bold(fy_row.fiscal_year)
					),
					title=_("Budget already submitted"),
				)
			doc.budget_distribution = []
		else:
			doc = frappe.new_doc("Budget")

		doc.budget_against = self._budget_dimension()
		if doc.budget_against == "Farm":
			doc.farm = self.farm
		doc.company = self.company
		doc.account = self.budget_account
		if self.cost_center:
			doc.cost_center = self.cost_center
		doc.from_fiscal_year = fy_row.fiscal_year
		doc.to_fiscal_year = fy_row.fiscal_year
		doc.distribution_frequency = "Monthly"
		doc.distribute_equally = 0
		doc.budget_amount = flt(fy_row.value)
		doc.flags.ignore_permissions = True
		doc.save()

		# Budget.before_save -> allocate_budget() always regenerates the distribution
		# on insert (is_new means there is no prior doc to compare against), which
		# flattens it to an even 1/12 per month. So write the seasonal profile on a
		# second save: by then nothing it watches has changed and distribute_equally
		# is 0, so it leaves these rows alone.
		doc.set("budget_distribution", [])
		for row, pct in self._balanced_percents(months):
			doc.append("budget_distribution", {
				"start_date": row.month_start,
				"end_date": row.month_end,
				"amount": flt(row.value),
				"percent": pct,
			})
		doc.save()
		return doc.name


# ---------------------------------------------------------------------------

def fiscal_year_available(fiscal_year, company):
	"""A Fiscal Year with no company rows is open to all companies."""
	if not fiscal_year or not company:
		return False
	rows = frappe.get_all("Fiscal Year Company", filters={"parent": fiscal_year},
	                      pluck="company")
	return (not rows) or (company in rows)


def fiscal_year_for(date, company=None):
	try:
		from erpnext.accounts.utils import get_fiscal_year

		return get_fiscal_year(date, company=company, as_dict=True).name
	except Exception:
		# Fall back to any fiscal year spanning the date, else the calendar year.
		rows = frappe.get_all(
			"Fiscal Year",
			filters={"year_start_date": ["<=", date], "year_end_date": [">=", date]},
			pluck="name",
			limit=1,
		)
		return rows[0] if rows else str(getattr(date, "year", date))


@frappe.whitelist()
def build_from_plan(production_plan):
	"""Create the monthly budget for an approved plan."""
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	if not plan.plan_months:
		frappe.throw(_("Plan {0} has no monthly rows to budget.").format(plan.name))

	doc = frappe.new_doc("Summer Flower Budget")
	doc.production_plan = plan.name
	doc.market_demand = plan.market_demand
	doc.variety = plan.variety
	doc.farm = plan.farm
	doc.company = plan.company
	doc.currency = plan.currency
	doc.price_per_stem = plan.price_per_stem
	doc.budget_account = default_income_account(plan.company)
	doc.cost_center = frappe.db.get_value("Company", plan.company, "cost_center")

	for m in plan.plan_months:
		doc.append("budget_months", {
			"year": m.year,
			"month": m.month,
			"stems": m.production_stems,
		})

	doc.insert(ignore_permissions=True)
	return doc.name


def default_income_account(company):
	if not company:
		return None
	acc = frappe.db.get_value("Company", company, "default_income_account")
	if acc:
		return acc
	rows = frappe.get_all(
		"Account",
		filters={"company": company, "root_type": "Income", "is_group": 0},
		pluck="name",
		limit=1,
	)
	return rows[0] if rows else None
