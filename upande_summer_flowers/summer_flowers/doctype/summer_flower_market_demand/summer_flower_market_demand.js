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

		draw_calendar(frm);
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
		primary_action_label: __("Check first"),
		primary_action(values) {
			// A plan is what a budget and a propagation plan hang off, so what it will
			// mean is shown before anything is written rather than discovered after.
			frappe.call({
				method: "upande_summer_flowers.summer_flowers.doctype"
					+ ".summer_flower_production_plan.summer_flower_production_plan"
					+ ".plan_preview",
				args: { market_demand: frm.doc.name, ...values },
				freeze: true,
			}).then((r) => {
				if (!r.message) return;
				d.hide();
				confirm_plan(frm, values, r.message);
			});
		},
	});
	d.show();
}

function confirm_plan(frm, values, s) {
	const num = (n) => frappe.format(n || 0, { fieldtype: "Int" });
	const rows = [
		[__("Variety"), s.variety],
		[__("Farm"), s.farm],
		[__("Season"), `${s.season} (${s.season_start} to ${s.season_end})`],
		[__("Demand in that season"), `${num(s.demand_stems)} stems over ${
			s.weeks} weeks, peak week ${num(s.peak_week_stems)}`],
		[__("Firm of that"), `${num(s.firm_stems)} stems`],
		[__("Protocol in force"), s.protocol || `<span style="color:#c0392b">${
			__("none")}</span>`],
		[__("Summer flower blocks at the farm"), num(s.blocks)],
	];
	if (s.lead) {
		rows.push([__("TC order deadline"),
			`${s.lead.order_by} (${s.lead.weeks_back} weeks before ${
				s.lead.first_demand_week})${s.lead.late
				? ` — <span style="color:#c0392b">${__("already passed")}</span>` : ""}`]);
	}
	// What saying yes costs: the plantlets to buy and the ground to find. Both are
	// knowable from the demand and the protocol, and both are why a plan gets
	// abandoned after it has been built.
	if (s.tc) {
		// Every figure named, in the order it is derived. "12,245 mother plants cutting
		// 12,245 in the peak week" repeated one number without saying the second was
		// cuttings — and at one cutting per plant per week they are always equal.
		const rate = s.tc.cuttings_per_plant_per_week || 1;
		rows.push([__("Tissue culture to buy"),
			`<b>${num(s.tc.plantlets)}</b> ${__("plantlets")}`]);
		rows.push([__("Which multiply into"),
			`${num(s.tc.mother_plants)} ${__("mother plants")}` +
			` <span class="text-muted">(${__("at")} ${rate} ${
				__("cutting(s) per plant per week")})</span>`]);
		rows.push([__("Enough to cut"),
			`${num(s.tc.cuttings)} ${__("cuttings")} ${__("in")} <b>${
				esc(s.tc.peak_week)}</b> — ${__("the busiest sticking week")}`]);
		rows.push([__("That week plants"),
			`${num(s.tc.plants_in_peak_week)} ${__("plants, to meet")} ${
				num(s.tc.peak_week_stems)} ${__("stems of demand")}`]);
	}
	if (s.space) {
		const over = s.space.pct_of_farm && s.space.pct_of_farm > 100;
		rows.push([__("Ground it needs"),
			`<b>${s.space.ha_needed} ha</b> ${__("standing")} (${
				num(s.space.beds_needed)} ${__("beds")}, ${
				num(s.space.plants_needed)} ${__("plants")})`]);
		rows.push([__("Ground at this farm"),
			`${s.space.ha_at_farm} ha, ${num(s.space.beds_at_farm)} ${__("beds")}` +
			(s.space.pct_of_farm
				? ` — <span style="color:${over ? "#c0392b" : "#27ae60"}">${
					s.space.pct_of_farm}% ${__("of it")}</span>` : "")]);
	}
	if (s.existing_plan) rows.push([__("Existing plan"), s.existing_plan]);
	if (s.propagation_plan) rows.push([__("Propagation plan"), s.propagation_plan]);

	let html = `<table class="table table-bordered table-sm" style="font-size:12px">${
		rows.map(([k, v]) => `<tr><td style="width:42%">${k}</td><td>${v}</td></tr>`).join("")
	}</table>`;
	(s.blocking || []).forEach((n) => {
		html += `<p style="color:#c0392b;margin:6px 0"><b>${__("Blocking")}:</b> ${n}</p>`;
	});
	(s.notes || []).forEach((n) => {
		html += `<p style="color:#8a6d3b;margin:6px 0">${n}</p>`;
	});

	const c = new frappe.ui.Dialog({
		title: __("Before creating this plan"),
		size: "large",
		fields: [{ fieldtype: "HTML", options: html }],
		primary_action_label: s.can_create ? __("Create the plan") : __("Close"),
		primary_action() {
			c.hide();
			if (!s.can_create) return;
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
	c.show();
}

function draw_calendar(frm) {
	const field = frm.get_field("calendar_html");
	if (!field || !window.sf_calendar) return;
	// Firm and forecast weeks are the same number until you know which is which, so
	// the firm ones are marked on their Monday rather than left to the grid.
	window.sf_calendar(field, {
		weeks: (frm.doc.demand_weeks || []).map((r) => ({
			date: r.week_start_date,
			value: r.demand_stems,
			title: `${r.year}-W${String(r.week_no).padStart(2, "0")}: ${
				frappe.format(r.demand_stems || 0, { fieldtype: "Int" })} stems${
				r.is_firm ? " (firm)" : " (forecast)"}`,
		})),
		events: (frm.doc.demand_weeks || []).filter((r) => r.is_firm).map((r) => ({
			date: r.week_start_date, colour: "#27ae60", title: __("Firm demand"),
		})),
		legend: [{ dot: "#27ae60", label: __("firm week") },
			{ label: __("shading: heavier weeks are darker") }],
		empty: __("No demand weeks yet. Extend the horizon to add some."),
	});
}
