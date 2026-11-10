// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Market Demand", {
	refresh(frm) {
		// What the fields mean, behind the "i" icon, instead of a paragraph under
		// each one.
		upande_summer_flowers.walkthrough.attach(frm, "Market Demand Walkthrough");

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
			// The one thing this form exists to lead to, so it is the primary action
			// rather than a line in the menu. Only styled as primary when there is no
			// horizon gap -- Extend Horizon owns that emphasis while the register is
			// still short, and two primary buttons tell the user nothing.
			const plan_btn = frm.add_custom_button(__("Create Production Plan"), () =>
				create_plan(frm)
			);
			plan_btn.removeClass("btn-default").addClass(
				frm.doc.horizon_gap_weeks > 0 ? "btn-secondary" : "btn-primary"
			);
			plan_btn.attr(
				"title",
				__("Work back from these weeks to the weeks to plant, stick and order.")
			);
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
	// A plan is one farm's commitment for one season, and the register already names
	// that farm, so only the season is asked for.
	const startYear = first.week_start_date
		? (new Date(first.week_start_date).getMonth() + 1 >= 7
			? new Date(first.week_start_date).getFullYear()
			: new Date(first.week_start_date).getFullYear() - 1)
		: first.year;
	const d = new frappe.ui.Dialog({
		title: __("Create Production Plan"),
		fields: [
			{
				fieldname: "farm_note",
				fieldtype: "HTML",
				// The register is one farm's order book, so the farm is not a choice
				// made here. It was asked for twice and could be answered differently
				// the second time, which planned a farm the demand never asked.
				options: __("Planning <b>{0}</b> at <b>{1}</b>.", [
					frappe.utils.escape_html(frm.doc.variety || ""),
					frappe.utils.escape_html(frm.doc.farm || ""),
				]),
			},
			{
				fieldname: "season_start_year",
				label: __("Season (starting year)"),
				// Picked, not typed. A free Int here is how five plans on this site
				// ended up with season 0, three of them submitted.
				fieldtype: "Link",
				options: "Fiscal Year",
				default: String(startYear),
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
	// Plain text, not frappe.format: the Int formatter returns markup that the
	// dialog laid out on its own line, so "0.06 ha standing (40 beds, 4,800
	// plants)" came apart into four.
	const num = (v) => (Number(v) || 0).toLocaleString("en-US");
	const ha = (v) => (Number(v) || 0).toFixed(2);
	const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
	const g = s.ground || {}, b = s.bench, r = s.route, L = s.losses;
	const sp = s.space || {};

	// Four numbers decide whether this plan can happen: what is wanted, what has
	// to be bought, whether there is ground, and whether there is bench. The
	// rest is detail and sits under them.
	const tileRow = [
		{ k: __("Demand"), v: num(s.demand_stems),
		  m: `${__("stems")} · ${__("peak")} ${num(s.peak_week_stems)}` },
		s.tc && { k: __("{0} to buy", [esc(s.tc.stage || "TC")]),
		  v: num(s.tc.units || s.tc.plantlets),
		  m: s.tc.pool
			? `${num(s.tc.mother_plants)} ${__("mother plants")}`
			: `${__("for")} ${num(sp.plants_needed || s.tc.plants_in_peak_week)} ${__("plants")}` },
		sp.ha_needed != null && { k: __("Ground"), v: `${ha(sp.ha_needed)} ha`,
		  m: `${num(sp.beds_needed)} ${__("beds")} · ${
			g.beds_free != null ? `${num(g.beds_free)} ${__("free")}` : ""}`,
		  bad: g.beds_free != null && sp.beds_needed > g.beds_free },
		b && { k: __("Raising"), v: num(b.decided ? b.allocated : b.needed),
		  m: b.decided
			? (b.short ? `${__("short")} ${num(b.short)} ${__("of bench")}`
				: `${num(b.free)} ${__("free of")} ${num(b.capacity)}`)
			: `${__("nobody chosen yet")} · ${num(b.free)} ${__("free of")} ${
				num(b.capacity)}`,
		  bad: !!(b.decided && b.short) },
	].filter(Boolean);

	const tiles = `<div class="pv-tiles">` + tileRow.map((t) =>
		`<div class="pv-tile${t.bad ? " pv-tile-bad" : ""}">` +
		`<div class="pv-tk">${t.k}</div><div class="pv-tv">${t.v}</div>` +
		`<div class="pv-tm">${t.m}</div></div>`).join("") + `</div>`;

	// One line each, because each is one idea. The route IS the plan: what is
	// bought, what it passes through, what it loses on the way.
	const facts = [];
	if (r) {
		const road = (r.stages || []).map((x) =>
			`<b>${esc(x.stage)}</b>${x.weeks ? ` ${x.weeks}w` : ""}${
				x.loss_pct ? ` <span class="pv-bad">−${x.loss_pct}%</span>` : ""}`)
			.join(" <span class='pv-dim'>→</span> ");
		const lifts = (r.lifts || []).length > 1
			? ` · ${__("handed over in")} ${r.lifts.length} ${__("lifts")}: ` +
			  r.lifts.map((x) => `${x.share_pct}% ${__("at")} ${x.weeks_after}w`).join(" · ")
			: "";
		facts.push([__("The route"), road + lifts]);
	}
	if (L) {
		facts.push([__("Losses"),
			`${__("Rooting")} ${L.rooting_pct}% × ${__("establishment")} ${
				L.establishment_pct}% = <b>${L.survival_pct}%</b> ${
				__("of what is bought stands up")}` +
			(L.per_plant ? ` <span class="pv-dim">(${L.per_plant} ${
				__("bought a standing plant")})</span>` : "") +
			(L.grading_pct ? ` · ${L.grading_pct}% ${__("of cut stems fail grading")}` : "")]);
	}
	if (sp.ha_needed != null) {
		const tight = g.beds_free != null && sp.beds_needed > g.beds_free;
		facts.push([__("Ground"),
			`${__("Needs")} <b>${num(sp.beds_needed)} ${__("beds")}</b> (${
				ha(sp.ha_needed)} ha) ${__("for")} ${num(sp.plants_needed)} ${
				__("plants standing")}` +
			(g.beds != null
				? ` · <span class="${tight ? "pv-bad" : "pv-good"}">${
					num(g.beds_free)} ${__("of")} ${num(g.beds)} ${__("beds free")}</span>` +
				  ` <span class="pv-dim">(${ha(g.ha_free)} ${__("of")} ${ha(g.ha)} ha, ${
					num(g.blocks)} ${__("blocks")}, ${num(g.blocks_in_use)} ${
					__("already in use")})</span>` : "") +
			((sp.beds_with_minimum || 0) - (sp.beds_if_divisible || 0) > 0
				? `<br><span class="pv-dim">${
					num(sp.beds_with_minimum - sp.beds_if_divisible)} ${
					__("of those beds are the cost of the minimum planting")} (${
					sp.min_planting_beds} ${__("beds")}, ${sp.min_planting_area_sqm} m²)</span>`
				: "")]);
	}
	if (b) {
		// Nobody has said yet who raises this. It may all go to an outside
		// propagator, in which case none of it touches our bench and a
		// shortfall against ours is not a fact about anything. Until the choice
		// is made this reads as the condition it is.
		const verdict = !b.decided
			? `<span class="pv-dim">${__("Nobody has been chosen to raise this yet")}</span>` +
			  (b.short_if_all_here
				? ` · <span class="pv-dim">${__("were it all raised on our own benches it would be")} ${
					num(b.short_if_all_here)} ${__("plants over")}</span>`
				: ` · <span class="pv-good">${__("our own benches could take all of it")}</span>`)
			: (b.short
				? `<span class="pv-bad">${__("Short")} ${num(b.short)} ${
					__("plants of bench")}</span>`
				: `<span class="pv-good">${__("Room for it")}</span>`);
		const unit = (u) =>
			`${esc(u.unit)} <span class="pv-dim">${num(u.free)}/${num(u.capacity)}` +
			`${u.here ? "" : ` · ${__("at")} ${esc(u.farm || __("another farm"))}`}</span>`;
		facts.push([__("Raising it"), verdict +
			` · ${b.units.slice(0, 3).map(unit).join(" · ")}` +
			(b.units.length > 3 ? ` <span class="pv-dim">+${b.units.length - 3}</span>` : "") +
			(b.units_here === 0
				? `<br><span class="pv-dim">${
					__("None of these benches is at this farm.")}</span>` : "")]);
	}
	if (s.lead) {
		facts.push([__("Order by"),
			`<b>${esc(s.lead.order_by)}</b> <span class="pv-dim">(${
				s.lead.weeks_back} ${__("weeks before")} ${esc(s.lead.first_demand_week)})</span>` +
			(s.lead.late ? ` — <span class="pv-bad">${__("already passed")}</span>` : "")]);
	}

	// The identity of the thing, kept small and last: nobody opens this dialog
	// to find out which variety they clicked on.
	const idRows = [
		[__("Variety"), esc(s.variety)],
		[__("Farm"), esc(s.farm)],
		[__("Season"), `${esc(s.season)} <span class="pv-dim">(${
			esc(s.season_start)} to ${esc(s.season_end)})</span>`],
		[__("Protocol"), s.protocol ? esc(s.protocol)
			: `<span class="pv-bad">${__("none in force")}</span>`],
		[__("Firm demand"), `${num(s.firm_stems)} ${__("of")} ${num(s.demand_stems)} ${__("stems")}`],
	];
	if (s.existing_plan) idRows.push([__("Existing plan"), esc(s.existing_plan)]);
	if (s.propagation_plan) idRows.push([__("Propagation plan"), esc(s.propagation_plan)]);

	const block = (s.blocking || []), note = (s.notes || []);
	const verdict = block.length
		? `<div class="pv-strip pv-strip-bad"><b>${__("Cannot be created yet")}</b> — ` +
		  `${block.length} ${block.length === 1 ? __("thing stands in the way")
			: __("things stand in the way")}</div>`
		: `<div class="pv-strip pv-strip-good"><b>${__("Ready to create")}</b>` +
		  (note.length ? ` — ${note.length} ${note.length === 1
			? __("thing to read first") : __("things to read first")}` : "") + `</div>`;

	let html = `<style>
	 .pv{font-size:12.5px;line-height:1.55}
	 .pv-strip{padding:9px 12px;border-radius:6px;margin:0 0 12px;font-size:13px}
	 .pv-strip-bad{background:#fdecea;color:#8c1d16;border-left:4px solid #d9534f}
	 .pv-strip-good{background:#eaf7ef;color:#1b5e34;border-left:4px solid #28a745}
	 .pv-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));
	   gap:8px;margin-bottom:14px}
	 .pv-tile{background:#f7f8fa;border:1px solid #eceef1;border-radius:6px;padding:8px 10px}
	 .pv-tile-bad{background:#fdecea;border-color:#f5c6c2}
	 .pv-tk{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#6b7280}
	 .pv-tv{font-size:17px;font-weight:600;line-height:1.25;margin:1px 0}
	 .pv-tm{font-size:11px;color:#6b7280}
	 .pv-f{display:grid;grid-template-columns:118px 1fr;gap:4px 12px;
	   padding:7px 0;border-top:1px solid #f0f1f3}
	 .pv-fk{color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
	   padding-top:2px}
	 .pv-sec{text-transform:uppercase;letter-spacing:.06em;font-size:10.5px;
	   color:#6b7280;margin:16px 0 4px;font-weight:600}
	 .pv-t{width:100%;border-collapse:collapse}
	 .pv-t th{width:38%;text-align:left;font-weight:400;color:#6b7280;
	   padding:4px 8px;border:1px solid #f0f1f3}
	 .pv-t td{padding:4px 8px;border:1px solid #f0f1f3}
	 .pv-dim{color:#8a8f98}
	 .pv-bad{color:#c0392b;font-weight:600}
	 .pv-good{color:#1e7e34;font-weight:600}
	 .pv-card{padding:8px 11px;border-radius:6px;margin:7px 0}
	 .pv-card-bad{background:#fdecea;border-left:3px solid #d9534f;color:#7f1d1d}
	 .pv-card-note{background:#fff8e6;border-left:3px solid #e0a800;color:#6b4f08}
	</style><div class="pv">` + verdict + tiles +
		facts.map(([k, v]) =>
			`<div class="pv-f"><div class="pv-fk">${k}</div><div>${v}</div></div>`).join("") +
		`<div class="pv-sec">${__("The plan itself")}</div>` +
		`<table class="pv-t">` + idRows.map(([k, v]) =>
			`<tr><th>${k}</th><td>${v}</td></tr>`).join("") + `</table>`;
	if (block.length) {
		html += `<div class="pv-sec">${__("Blocking")}</div>` + block.map((n) =>
			`<div class="pv-card pv-card-bad">${n}</div>`).join("");
	}
	if (note.length) {
		html += `<div class="pv-sec">${__("Worth reading")}</div>` + note.map((n) =>
			`<div class="pv-card pv-card-note">${n}</div>`).join("");
	}
	html += `</div>`;

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
