// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Budget", {
	refresh(frm) {
		frm.set_query("budget_account", () => ({
			filters: { company: frm.doc.company, root_type: "Income", is_group: 0 },
		}));
		frm.set_query("cost_center", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));

		if (frm.is_new()) return;

		if (frm.doc.budget_status !== "Posted to Accounts") {
			frm.add_custom_button(__("Post to Accounts"), () => post(frm)).addClass(
				"btn-primary"
			);
		} else {
			frm.dashboard.set_headline(
				__("Posted to accounts — {0} fiscal year(s).", [
					(frm.doc.fiscal_years || []).length,
				]),
				"green"
			);
		}

		if (frm.doc.production_plan) {
			frm.add_custom_button(__("View Plan"), () =>
				frappe.set_route(
					"Form",
					"Summer Flower Production Plan",
					frm.doc.production_plan
				)
			);
		}
	},
});

function post(frm) {
	if (!frm.doc.budget_account) {
		frappe.msgprint({
			title: __("Income Account required"),
			message: __("Set the Income Account on the Accounts Posting section first."),
			indicator: "red",
		});
		return;
	}

	frappe.confirm(
		__(
			"Write a Monthly Distribution and a Budget for each of the {0} fiscal years covered? Budgets are left in draft for finance to review.",
			[(frm.doc.fiscal_years || []).length]
		),
		() =>
			frm
				.call({
					doc: frm.doc,
					method: "post_to_accounts",
					freeze: true,
					freeze_message: __("Posting to accounts..."),
				})
				.then((r) => {
					const rows = r.message || [];
					frappe.msgprint({
						title: __("Posted"),
						indicator: "green",
						message: rows
							.map((x) =>
								__("{0}: distribution {1}, budget {2}", [
									x.fiscal_year,
									x.monthly_distribution,
									frappe.utils.get_form_link("Budget", x.budget, true),
								])
							)
							.join("<br>"),
					});
					frm.reload_doc();
				})
	);
}
