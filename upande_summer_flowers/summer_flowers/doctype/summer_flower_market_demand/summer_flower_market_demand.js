// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Market Demand", {
	refresh(frm) {
		frm.set_query("variety", () => ({ filters: { item_group: "Summer Flowers" } }));

		if (frm.is_new()) return;

		if (frm.doc.horizon_gap_weeks > 0) {
			frm.dashboard.set_headline(
				__("Horizon is short by {0} weeks of the {1}-year target.", [
					frm.doc.horizon_gap_weeks,
					frm.doc.target_years_ahead,
				]),
				"orange"
			);
			frm.add_custom_button(__("Extend Horizon"), () => extend_horizon(frm)).addClass(
				"btn-primary"
			);
		} else if (frm.doc.weeks_covered) {
			frm.dashboard.set_headline(
				__("Horizon reaches {0} — {1} weeks ahead.", [
					frm.doc.horizon_end,
					frm.doc.weeks_ahead_of_today,
				]),
				"green"
			);
		}

		if (frm.doc.weeks_covered) {
			frm.add_custom_button(__("Create Production Plan"), () => create_plan(frm));
		}
	},
});

function extend_horizon(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Extend Demand Horizon"),
		fields: [
			{
				fieldname: "weeks",
				label: __("Weeks to add"),
				fieldtype: "Int",
				default: frm.doc.horizon_gap_weeks,
				reqd: 1,
			},
			{
				fieldname: "copy_from_last_year",
				label: __("Seed each week from the same week last year"),
				fieldtype: "Check",
				default: 1,
				description: __(
					"Starts the new weeks from last year's shape instead of zero. You still edit them."
				),
			},
		],
		primary_action_label: __("Extend"),
		primary_action(values) {
			d.hide();
			frm.call({
				doc: frm.doc,
				method: "extend_horizon",
				args: values,
				freeze: true,
				freeze_message: __("Extending horizon..."),
			}).then((r) => {
				if (r.message) {
					frappe.show_alert({ message: r.message.message, indicator: "green" });
				}
				frm.reload_doc();
			});
		},
	});
	d.show();
}

function create_plan(frm) {
	const first = frm.doc.demand_weeks[0];
	// A plan is one farm's commitment for one season. The register is per variety, so
	// the farm is chosen here; the season decides the weekly grid, which is why the
	// year and week are no longer asked for.
	const startYear = first.week_start_date
		? (new Date(first.week_start_date).getMonth() + 1 >= 7
			? new Date(first.week_start_date).getFullYear()
			: new Date(first.week_start_date).getFullYear() - 1)
		: first.year;
	const d = new frappe.ui.Dialog({
		title: __("Create Production Plan"),
		fields: [
			{
				fieldname: "farm",
				label: __("Farm"),
				fieldtype: "Link",
				options: "Farm",
				reqd: 1,
				description: __("Which farm grows this. It decides the blocks available and the protocol version that applies."),
			},
			{
				fieldname: "season_start_year",
				label: __("Season (starting year)"),
				fieldtype: "Int",
				default: startYear,
				reqd: 1,
				description: __("{0} means 1 July {0} to 30 June {1}.", [startYear, startYear + 1]),
			},
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			d.hide();
			frm.call({
				doc: frm.doc,
				method: "create_production_plan",
				args: values,
				freeze: true,
				freeze_message: __("Building production plan..."),
			}).then((r) => {
				if (r.message) {
					frappe.set_route("Form", "Summer Flower Production Plan", r.message);
				}
			});
		},
	});
	d.show();
}
