// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Production Plan", {
	refresh(frm) {
		// Only this crop at this farm. A protocol version for another variety would
		// produce numbers that look fine and mean nothing, so it is not offered.
		frm.set_query("protocol", () => ({
			filters: {
				variety: frm.doc.variety,
				farm: frm.doc.farm,
				version_status: ["in", ["Active", "Superseded"]],
			},
		}));

		if (frm.is_new()) return;

		render_sheet(frm);
		draw_calendar(frm);
		frm.add_custom_button(__("Download Planning Sheet"), () => {
			open_url_post(
				"/api/method/upande_summer_flowers.summer_flowers.plan_sheet.sheet_csv",
				{ plan: frm.doc.name },
				true
			);
		});
		frm.add_custom_button(__("Edit Crop Protocol"), () => {
			// The assumptions at the foot of the sheet are this protocol. Editing it
			// puts it back to Draft for re-approval, which then writes a new version
			// and this plan can be regenerated against it.
			frappe.set_route("Form", "Summer Flower Protocol",
				`${frm.doc.variety}-${frm.doc.farm}`);
		});

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Regenerate"), () => {
				frappe.confirm(
					__("Rebuild the weekly grid and planting proposals from current demand and plantings?"),
					() =>
						frm
							.call({
								doc: frm.doc,
								method: "regenerate",
								freeze: true,
								freeze_message: __("Regenerating..."),
							})
							.then(() => frm.reload_doc())
				);
			});
		}

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Create Plantings"), () =>
				frm
					.call({
						doc: frm.doc,
						method: "create_plantings",
						freeze: true,
						freeze_message: __("Creating plantings..."),
					})
					.then((r) => {
						const m = r.message || {};
						frappe.msgprint({
							title: __("Plantings"),
							indicator: (m.created || []).length ? "green" : "orange",
							message:
								__("Created {0}.", [(m.created || []).length]) +
								((m.skipped || []).length
									? "<br><br>" +
									  __("Skipped {0}:", [m.skipped.length]) +
									  "<br>" +
									  m.skipped.join("<br>")
									: ""),
						});
						frm.reload_doc();
					})
			);
		}

		if (frm.doc.budget) {
			frm.add_custom_button(__("View Budget"), () =>
				frappe.set_route("Form", "Summer Flower Budget", frm.doc.budget)
			);
		}

		set_headline(frm);
		// A submitted plan never runs validate again, so its stored verdict is
		// frozen at approval. Ask for the live one.
		if (frm.doc.docstatus === 1) {
			frm.call({ doc: frm.doc, method: "get_protocol_freshness" }).then((r) => {
				const f = r.message || {};
				if (f.stale) {
					frm.dashboard.add_comment(f.note.replace(/\n/g, " "), "orange", true);
				}
			});
		}
	},

	protocol(frm) {
		// Changing the version does not change a single stored row until the plan is
		// rebuilt, so say so at the moment of the change rather than letting the old
		// numbers sit there under a new protocol name.
		if (frm.doc.protocol && frm.doc.plan_weeks && frm.doc.plan_weeks.length) {
			frm.dashboard.clear_headline();
			frappe.show_alert({
				message: __("Save, then Regenerate, to rebuild the plan on {0}.", [
					frm.doc.protocol,
				]),
				indicator: "orange",
			});
		}
	},
});

function set_headline(frm) {
	const parts = [];

	if (frm.doc.protocol_stale) {
		frm.dashboard.add_comment(
			__("These numbers were built on an older reading of {0}. Regenerate to apply it.", [
				frm.doc.protocol,
			]),
			"orange",
			true
		);
	}

	if (frm.doc.total_demand_stems) {
		parts.push(
			__("Coverage {0}%", [format_number(frm.doc.coverage_pct, null, 1)])
		);
	}
	if (frm.doc.weeks_in_deficit) {
		parts.push(
			__("{0} weeks in deficit, worst {1} stems", [
				frm.doc.weeks_in_deficit,
				format_number(frm.doc.worst_weekly_deficit, null, 0),
			])
		);
	}
	if (frm.doc.peak_weekly_sticking) {
		// This is the number that sizes the motherstock -- not the annual total.
		parts.push(
			__("Peak sticking {0} in {1}", [
				format_number(frm.doc.peak_weekly_sticking, null, 0),
				frm.doc.peak_sticking_week,
			])
		);
	}

	if (!parts.length) return;

	const indicator = frm.doc.weeks_in_deficit ? "orange" : "green";
	frm.dashboard.set_headline(parts.join(" &nbsp;·&nbsp; "), indicator);

	if (frm.doc.weeks_in_deficit) {
		frm.dashboard.add_comment(
			__(
				"Weekly variance swings hard because a whole block flushes in one week. Read the Monthly tab for the reliable picture."
			),
			"blue",
			true
		);
	}
}

function fmt(n) {
	if (n === "" || n === null || n === undefined) return "";
	if (!n) return "0";
	return frappe.format(n, { fieldtype: "Int" });
}

function render_sheet(frm) {
	const wrap = frm.get_field("matrix_html").$wrapper;
	wrap.html(`<div class="text-muted">${__("Loading sheet...")}</div>`);
	frappe.call({
		method: "upande_summer_flowers.summer_flowers.plan_sheet.sheet",
		args: { plan: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			wrap.html(sheet_html(r.message));
		},
	});
}

function sheet_html(d) {
	const head = ["BLOCK", "Planting wk", "Planting Date", "Pinching Date",
		"NET AREA (m2)", "Uprooting Date", "NET AREA (Ha)", "NO OF BEDS", "NO.PLANTS"];
	const cols = d.grid.length;
	let h = `<div style="overflow-x:auto"><table class="table table-bordered table-sm"
		style="font-size:11px;white-space:nowrap">`;

	h += `<thead><tr><th colspan="${head.length}"></th>`;
	d.grid.forEach((g) => (h += `<th class="text-center">${g.year}</th>`));
	h += `<th></th><th></th></tr><tr>`;
	head.forEach((c) => (h += `<th>${c}</th>`));
	d.grid.forEach((g) => (h += `<th class="text-center">${g.week}</th>`));
	h += `<th>TOTAL</th><th>STEMS/<br>HA</th><th>LIFETIME</th></tr></thead><tbody>`;

	// A planting with no block produces nothing and has to look different from one
	// that produces nothing because its weeks are outside the season.
	d.plantings.forEach((p) => {
		const style = p.not_placed ? ' style="background:#fff5f5"' :
			(p.is_new ? "" : ' style="background:#f7f7f7"');
		h += `<tr${style}><td>${p.block}</td><td>${p.planting_week || ""}</td>
			<td>${p.planting_date}</td><td>${p.pinch_date}</td>
			<td class="text-right">${fmt(p.net_area_sqm)}</td><td>${p.uproot_date}</td>
			<td class="text-right">${((p.net_area_sqm || 0) / 10000).toFixed(4)}</td>
			<td class="text-right">${fmt(p.beds)}</td>
			<td class="text-right">${fmt(p.plants)}</td>`;
		p.weekly.forEach((v) => (h += `<td class="text-right">${v ? fmt(v) : ""}</td>`));
		h += `<td class="text-right"><b>${fmt(p.season_total)}</b></td>
			<td class="text-right">${fmt(Math.round(p.season_stems_per_ha))}</td>
			<td class="text-right">${fmt(p.lifetime_total)}</td></tr>`;
	});

	const t = d.totals;
	h += `<tr style="font-weight:bold"><td>TOTAL</td><td></td><td></td><td></td>
		<td class="text-right">${fmt(t.net_area_sqm)}</td><td></td>
		<td class="text-right">${((t.net_area_sqm || 0) / 10000).toFixed(4)}</td>
		<td class="text-right">${fmt(t.beds)}</td>
		<td class="text-right">${fmt(t.plants)}</td>
		<td colspan="${cols}"></td>
		<td class="text-right">${fmt(t.season)}</td>
		<td class="text-right">${fmt(Math.round(t.season_stems_per_ha))}</td>
		<td class="text-right">${fmt(t.lifetime)}</td></tr>`;

	const band = (label, values, opts = {}) => {
		let row = `<tr${opts.style || ""}><td colspan="${head.length}">${label}</td>`;
		for (let i = 0; i < cols; i++) {
			const v = values[i];
			const neg = opts.signed && v < 0;
			row += `<td class="text-right"${neg ? ' style="color:#c0392b"' : ""}>${
				v === 0 && opts.blankZero ? "" : fmt(v)
			}</td>`;
		}
		return row + `<td colspan="3"></td></tr>`;
	};

	h += band(__("Weekly production"), d.weekly_production, { style: ' style="font-weight:bold"' });
	h += band(__("Monthly production"), d.monthly_production, { blankZero: true });
	d.grades.forEach((g) => {
		h += g.weekly.length
			? band(`${g.grade} (${g.pct}%)`, g.weekly)
			: `<tr><td colspan="${head.length}">${g.grade}</td>
				<td colspan="${cols + 3}" class="text-muted">${
					__("No allocation % on the protocol, so this grade cannot be split out.")
				}</td></tr>`;
	});
	h += band(__("Weekly market demand"), d.weekly_demand);
	h += band(__("Monthly market demand"), d.monthly_demand, { blankZero: true });
	h += band(__("DIFFERENCE"), d.difference, { signed: true, style: ' style="font-weight:bold"' });
	h += band(__("Area (ha standing)"), d.area.map((v) => Math.round(v * 10000) / 10000));
	h += `</tbody></table></div>`;

	const a = d.assumptions || {};
	if (a.flush_schedule) {
		h += `<div style="margin-top:12px"><b>${
			__("{0} assumptions", [d.variety])
		}</b> <span class="text-muted">${
			__("from Crop Protocol {0} -- change them there", [d.protocol])
		}</span>
		<table class="table table-bordered table-sm" style="font-size:11px;max-width:760px">
		<tr><td>${__("m² per bed")}</td>
			<td class="text-right">${a.net_sqm_per_bed}</td><td></td></tr>
		<tr><td>${__("Plants per m² (net) / per bed")}</td><td class="text-right">${
			a.plants_per_sqm_net} / ${fmt(a.plants_per_bed)}</td>
			<td class="text-muted">${fmt(a.plants_per_net_ha)} ${__("per hectare")}</td></tr>
		<tr><td>${__("Pinch at week / total weeks in ground")}</td>
			<td class="text-right">${a.pinch_at_week} / ${a.total_weeks_in_ground}</td>
			<td class="text-muted">${(a.flushes_per_year || 0).toFixed(2)} ${
			__("flushes per year")}</td></tr>
		</table>
		<table class="table table-bordered table-sm" style="font-size:11px;max-width:760px">
		<thead><tr><th>${__("Flush")}</th><th>${__("Weeks from pinch")}</th>
			<th>${__("Weeks from planting")}</th><th class="text-right">${
			__("Stems/plant")}</th><th class="text-right">${
			__("Stems/ha/yr")}</th></tr></thead><tbody>`;
		a.flush_schedule.forEach((f) => {
			h += `<tr><td>${f.flush}</td><td>${f.weeks_from_pinch}</td>
				<td>${f.weeks_from_planting}</td>
				<td class="text-right">${f.stems_per_plant}</td>
				<td class="text-right">${fmt(Math.round(f.stems_per_ha_year))}</td></tr>`;
		});
		h += `<tr style="font-weight:bold"><td colspan="3">${
			__("Total stems per plant life")}</td>
			<td class="text-right">${a.total_stems_per_plant_life}</td>
			<td class="text-right">${fmt(Math.round(a.stems_per_net_ha_life))}</td></tr>
			<tr><td colspan="3">${__("Per year")}</td><td></td>
			<td class="text-right">${fmt(Math.round(a.stems_per_net_ha_year))}</td></tr>
			<tr><td colspan="3">${__("Best year")}</td>
			<td class="text-right">${a.best_year_stems_per_plant}</td>
			<td class="text-right">${fmt(Math.round(a.best_year_stems_per_net_ha))}</td></tr>
			</tbody></table></div>`;
	}
	return h;
}

function draw_calendar(frm) {
	const field = frm.get_field("calendar_html");
	if (!field || !window.sf_calendar) return;
	const int = (n) => frappe.format(n || 0, { fieldtype: "Int" });

	// Variance rather than production, because the question a calendar answers is
	// which weeks are short -- and a signed scale can say that in colour.
	const weeks = (frm.doc.plan_weeks || []).map((w) => ({
		date: w.week_start_date,
		value: w.variance_stems,
		title: `${w.year}-W${String(w.week_no).padStart(2, "0")}: demand ${
			int(w.demand_stems)}, production ${int(w.production_stems)}, variance ${
			int(w.variance_stems)}`,
	}));

	// A planting is four dated moments, not one, and they are weeks apart: the day it
	// is stuck, the day it goes in the ground, the day it is pinched and the day it
	// first cuts. Reading them off a table meant counting weeks by hand.
	const events = [];
	(frm.doc.plan_blocks || []).forEach((b) => {
		const where = b.block || __("no block yet");
		if (b.planting_date) {
			events.push({ date: b.planting_date, colour: b.not_placed ? "#c0392b" : "#2980b9",
				title: `${__("Plant")} ${int(b.beds)} ${__("beds")} — ${where}` });
		}
		if (b.pinch_date) {
			events.push({ date: b.pinch_date, colour: "#f39c12",
				title: `${__("Pinch")} — ${where}` });
		}
	});
	if (frm.doc.tc_order_by_date) {
		events.push({ date: frm.doc.tc_order_by_date, colour: "#8e44ad",
			title: `${__("TC order deadline")}: ${int(frm.doc.tc_plants_to_order)} ${
				__("plantlets")}` });
	}

	window.sf_calendar(field, {
		weeks, events, signed: true,
		legend: [{ dot: "#2980b9", label: __("planting") },
			{ dot: "#c0392b", label: __("planting with no block") },
			{ dot: "#f39c12", label: __("pinch") },
			{ dot: "#8e44ad", label: __("TC order deadline") },
			{ label: __("red weeks are short of demand, green weeks are over") }],
		empty: __("No weeks yet. Regenerate the plan."),
	});
}
