// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Propagation Plan", {
	refresh(frm) {
		draw_calendar(frm);
		actual_buttons(frm);
		draw_overview(frm);
	},
});

function draw_calendar(frm) {
	const field = frm.get_field("calendar_html");
	if (!field || !window.sf_calendar) return;
	const int = (n) => frappe.format(n || 0, { fieldtype: "Int" });

	// Cuttings required rather than plants stuck, because the shortfall is the thing
	// worth seeing on a calendar: which weeks the pool cannot cover.
	const weeks = (frm.doc.weeks || []).map((r) => ({
		date: r.week_start_date,
		value: r.cuttings_required,
		title: `${r.year}-W${String(r.week_no).padStart(2, "0")}: ${
			int(r.plants_to_stick)} ${__("plants")}, ${int(r.cuttings_required)} ${
			__("cuttings")}${r.shortfall ? `, ${__("short")} ${int(r.shortfall)}` : ""}${
			r.notes ? " — " + r.notes : ""}`,
	}));

	const events = [];
	(frm.doc.weeks || []).filter((r) => r.shortfall).forEach((r) => {
		events.push({ date: r.week_start_date, colour: "#c0392b",
			title: `${__("Short by")} ${int(r.shortfall)} ${__("cuttings")}` });
	});
	// The four dates the whole order hangs on. The first is the one that expires.
	const mark = (date, colour, title) => date && events.push({ date, colour, title });
	mark(frm.doc.tc_order_date, "#8e44ad",
		`${__("Order")} ${int(frm.doc.tc_plants_required)} ${__("TC plantlets")}`);
	mark(frm.doc.tc_on_farm_date, "#2980b9", __("Plantlets on farm"));
	mark(frm.doc.first_sticking_date, "#16a085", __("First cutting taken"));
	mark(frm.doc.full_capacity_date, "#27ae60", __("Pool at full cutting capacity"));

	window.sf_calendar(field, {
		weeks, events,
		legend: [{ dot: "#8e44ad", label: __("TC order") },
			{ dot: "#2980b9", label: __("plantlets on farm") },
			{ dot: "#16a085", label: __("first cut") },
			{ dot: "#27ae60", label: __("full capacity") },
			{ dot: "#c0392b", label: __("week short of cuttings") },
			{ label: __("shading: heavier sticking weeks are darker") }],
		empty: __("No sticking weeks yet."),
	});
}


function actual_buttons(frm) {
	if (frm.is_new()) return;
	const group = __("Actuals");

	frm.add_custom_button(__("Record What Happened"), () => {
		frappe.new_doc("Summer Flower Propagation Actual", {
			propagation_plan: frm.doc.name,
		});
	}, group);

	frm.add_custom_button(__("See What Was Recorded"), () => {
		frappe.set_route("List", "Summer Flower Propagation Actual", {
			variety: frm.doc.variety,
			season_start_year: frm.doc.season_start_year,
		});
	}, group);

	// Only where there is something to accept. A button that always throws is
	// a button that teaches people to ignore buttons.
	if (cint(frm.doc.replan_cover) > 0) {
		frm.add_custom_button(__("Accept The Re-plan"), () => {
			frappe.confirm(
				__("Add {0} plants across the weeks still to come, to cover the {1} the closed weeks are short? Nothing already delivered changes.",
					[format_number(frm.doc.replan_cover),
						format_number(frm.doc.carried_short)]),
				() => frm.call("accept_replan").then((r) => {
					if (!r || !r.message) return;
					frappe.show_alert({
						message: __("{0} weeks took in {1} plants.",
							[r.message.accepted, format_number(r.message.plants)]),
						indicator: "green",
					});
					frm.reload_doc();
				}));
		}, group).addClass("btn-primary");
	}
}


// A propagation plan is the middle of a chain. On its own it says only what
// this unit sticks -- not who else was allocated the same season, not what was
// bought to make it possible, and not when the plants it raises actually go in
// the ground. Those three lived on three other documents.
function draw_overview(frm) {
	const field = frm.get_field("sfpp_overview_html");
	if (!field || frm.is_new()) return;
	frm.call({ doc: frm.doc, method: "overview_for_form" })
		.then((r) => paint_overview(field.$wrapper, frm, r && r.message));
}

function paint_overview(el, frm, o) {
	if (!o) { el.empty(); return; }
	const esc = frappe.utils.escape_html;
	const int = (n) => format_number(n || 0, null, 0);
	const day = (d) => (d ? frappe.datetime.str_to_user(d) : "—");
	const link = (dt, dn) =>
		`<a href="/app/${frappe.router.slug(dt)}/${encodeURIComponent(dn)}">${esc(dn)}</a>`;

	// ── who is raising this season ───────────────────────────────────────────
	const a = o.allocations || {};
	const arows = a.rows || [];
	let alloc;
	if (!arows.length) {
		alloc = `<p class="sfo-empty">${__("Nothing allocated yet. Open the procurement plan and choose propagation units.")}</p>`;
	} else {
		const body = arows.map((r) => {
			// The row for THIS plan is the one the reader is standing in; the
			// others are context. Marking it is the difference between "my
			// share" and "somebody's share".
			const me = r.is_this_plan;
			return `<tr${me ? ' class="sfo-me"' : ""}>` +
				`<td>${esc(r.propagation_unit || r.supplier || "—")}${
					r.supplier && !r.propagation_unit
						? ` <span class="sfo-tag">${__("outside")}</span>` : ""}${
					me ? ` <span class="sfo-tag">${__("this plan")}</span>` : ""}</td>` +
				`<td>${esc(r.entry_stage || "—")}</td>` +
				`<td class="r">${flt(r.share_pct).toFixed(2)}%</td>` +
				`<td class="r">${int(r.plants)}</td>` +
				`<td class="r">${r.planted ? int(r.planted) : "—"}</td>` +
				`<td class="r">${r.surplus ? int(r.surplus) : "—"}</td>` +
				`<td class="r">${r.troughs_used ? int(r.troughs_used) : "—"}</td>` +
				`<td>${r.propagation_plan ? link("Summer Flower Propagation Plan", r.propagation_plan) : "—"}</td>` +
				`</tr>`;
		}).join("");
		// What nobody took has to be bought in as plants -- that is the number
		// this table exists to surface, so it is stated rather than left to be
		// worked out from the shares.
		const foot = a.to_buy > 0
			? `<p class="sfo-note sfo-warn">${__("{0} of {1} plants allocated across {2} units. {3} must be bought in as plants.",
				[int(a.allocated), int(a.season_plants), a.units || 0, int(a.to_buy)])}</p>`
			: `<p class="sfo-note">${__("{0} of {1} plants allocated across {2} units.",
				[int(a.allocated), int(a.season_plants), a.units || 0])}</p>`;
		alloc = `<div class="sfo-scroll"><table class="sfo-t"><thead><tr>
			<th>${__("Propagation unit")}</th><th>${__("Bought as")}</th>
			<th class="r">${__("Share")}</th><th class="r">${__("Plants")}</th>
			<th class="r">${__("Planted")}</th><th class="r">${__("Surplus")}</th>
			<th class="r">${__("Troughs")}</th><th>${__("Its plan")}</th>
		</tr></thead><tbody>${body}</tbody></table></div>${foot}`;
	}

	// ── what was bought ──────────────────────────────────────────────────────
	const procs = o.procurement || [];
	const proc = !procs.length
		? `<p class="sfo-empty">${__("No procurement plan for this production plan yet.")}</p>`
		: `<div class="sfo-scroll"><table class="sfo-t"><thead><tr>
			<th>${__("Plan")}</th><th>${__("Bought as")}</th><th>${__("Method")}</th>
			<th>${__("Supplier")}</th><th class="r">${__("Units")}</th>
			<th class="r">${__("Plants to stand")}</th>
			<th>${__("Order by")}</th><th>${__("Status")}</th>
		</tr></thead><tbody>${procs.map((p) =>
			`<tr${p.orders_late ? ' class="sfo-late"' : ""}>` +
			`<td>${link("Summer Flower Procurement Plan", p.name)}</td>` +
			`<td>${esc(p.entry_stage || "—")}</td>` +
			`<td>${esc(p.method || "—")}</td>` +
			`<td>${esc(p.supplier || "—")}</td>` +
			`<td class="r">${int(p.total_units_to_order)}</td>` +
			`<td class="r">${int(p.total_plants_at_field)}</td>` +
			`<td>${day(p.first_order_by)}${p.orders_late
				? ` <span class="sfo-tag sfo-tag-red">${__("{0} late", [p.orders_late])}</span>` : ""}</td>` +
			`<td>${esc(p.status || "")}</td></tr>`).join("")}</tbody></table></div>`;

	// ── when it goes in the ground ───────────────────────────────────────────
	const pl = o.planting || {};
	const prows = pl.rows || [];
	let planting;
	if (pl.error) {
		planting = `<p class="sfo-empty">${esc(pl.error)}</p>`;
	} else if (!prows.length) {
		planting = `<p class="sfo-empty">${__("The production plan has no plantings scheduled.")}</p>`;
	} else {
		// Head and tail only. A season is fifty-odd plantings and the question
		// this answers is "when does this start and when does it end", not
		// "list them all" -- the production plan does that in full.
		const show = prows.length > 14
			? prows.slice(0, 7).concat([null]).concat(prows.slice(-7))
			: prows;
		const body = show.map((r) => r === null
			? `<tr class="sfo-gap"><td colspan="6">${__("… {0} more plantings …", [prows.length - 14])}</td></tr>`
			: `<tr${r.in_past ? ' class="sfo-late"' : ""}><td>${esc(r.plant || "—")}</td>` +
			  `<td>${day(r.planting_date)}</td>` +
			  `<td>${r.block ? esc(r.block)
					: `<span class="sfo-warn">${__("not placed")}</span>`}</td>` +
			  `<td class="r">${int(r.plants)}</td>` +
			  `<td class="r">${int(r.beds)}</td>` +
			  `<td>${esc(r.first_harvest || "—")}</td></tr>`).join("");
		const t = pl.totals || {};
		planting = `<div class="sfo-scroll"><table class="sfo-t"><thead><tr>
			<th>${__("Planting week")}</th><th>${__("Date")}</th><th>${__("Where")}</th>
			<th class="r">${__("Plants")}</th><th class="r">${__("Beds")}</th>
			<th>${__("First harvest")}</th>
		</tr></thead><tbody>${body}</tbody></table></div>` +
			`<p class="sfo-note">${__("{0} plantings · {1} plants · {2} beds",
				[prows.length, int(t.plants), int(t.beds)])}${
				t.unallocated ? ` · <span class="sfo-warn">${__("{0} not placed on a block", [t.unallocated])}</span>` : ""}${
				pl.protocol_stale ? ` · <span class="sfo-warn">${__("the protocol has moved since this was built")}</span>` : ""}</p>`;
	}

	el.html(`<style>
.sfo-wrap{margin:0 0 6px}
.sfo-wrap h4{font-size:.74rem;text-transform:uppercase;letter-spacing:.5px;
  color:var(--text-muted);margin:16px 0 7px}
.sfo-wrap h4:first-child{margin-top:0}
table.sfo-t{width:100%;border-collapse:collapse;font-size:.78rem}
table.sfo-t th{text-align:left;font-size:.64rem;text-transform:uppercase;letter-spacing:.5px;
  color:var(--text-muted);padding:5px 8px;border-bottom:1px solid var(--border-color)}
table.sfo-t td{padding:4px 8px;border-bottom:1px solid var(--border-color);
  font-variant-numeric:tabular-nums}
table.sfo-t th.r,table.sfo-t td.r{text-align:right}
table.sfo-t tr.sfo-me td{background:var(--bg-light-gray,#f4f5f6);font-weight:600}
table.sfo-t tr.sfo-late td{color:var(--red-600,#c0392b)}
table.sfo-t tr.sfo-gap td{text-align:center;color:var(--text-muted);font-style:italic}
.sfo-tag{font-size:.62rem;text-transform:uppercase;letter-spacing:.4px;
  border:1px solid var(--border-color);border-radius:9px;padding:1px 6px;margin-left:5px;
  color:var(--text-muted);font-weight:400}
.sfo-tag-red{color:var(--red-600,#c0392b);border-color:currentColor}
.sfo-scroll{max-height:300px;overflow:auto;border:1px solid var(--border-color);border-radius:8px}
.sfo-note{font-size:.76rem;color:var(--text-muted);margin:6px 0 0}
.sfo-warn{color:var(--orange-600,#b35309)}
.sfo-empty{font-size:.8rem;color:var(--text-muted);margin:0;padding:10px 0}
</style>
<div class="sfo-wrap">
  <h4>${__("Allocated across the propagation units")}</h4>${alloc}
  <h4>${__("What was bought")}</h4>${proc}
  <h4>${__("When it goes in the ground")}</h4>${planting}
</div>`);
}
