// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// An approved procurement plan stops being a decision and becomes a set of
// dates. What it buys, whether that meets the demand, when the material lands
// and when the first stems come off are the questions anybody asks of one, and
// answering them meant opening the production plan, the propagation plan and
// the planting calendar in three tabs.
frappe.ui.form.on("Summer Flower Procurement Plan", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) {
			frm.get_field("overview_html").$wrapper.empty();
			return;
		}
		frm.call({ doc: frm.doc, method: "overview_for_form" })
			.then((r) => sf_proc_overview(frm, r.message));
	},
});

function sf_proc_overview(frm, o) {
	const el = frm.get_field("overview_html").$wrapper;
	if (!o) { el.empty(); return; }
	const esc = frappe.utils.escape_html;
	const int = (n) => format_number(n || 0, null, 0);
	const day = (d) => (d ? frappe.datetime.str_to_user(d) : "—");

	const tile = (k, v, m, c) =>
		`<div class="sfp-tile"><div class="k">${esc(k)}</div>` +
		`<div class="v"${c ? ` style="color:var(${c})"` : ""}>${v}</div>` +
		`<div class="m">${m || ""}</div></div>`;

	const b = o.buying, dm = o.demand, w = o.when, g = o.ground;
	const cov = dm.coverage_pct || 0;
	const after = dm.after_risk_pct;

	const tiles = [
		tile(__("Buying"), int(b.units),
			`${esc(b.entry_stage || b.method || "")}` +
			(b.overridden ? ` · ${__("set by hand, sum said {0}", [int(b.calculated)])}` : "") +
			(b.supplier ? ` · ${esc(b.supplier)}` : ""),
			b.overridden ? "--warning" : ""),
		tile(__("Order by"), day(b.order_by),
			b.order_late ? __("that date has passed") : __("to arrive in time"),
			b.order_late ? "--critical" : "--good"),
	];
	if (b.propagates_here) {
		tiles.push(tile(__("Pool it builds"), int(b.pool),
			__("mother plants · {0} cuttings a week · {1} cycles",
				[int(b.weekly_draw), b.cycles])));
		tiles.push(tile(__("Cutting weeks"),
			b.cutting_weeks == null ? "—" : int(b.cutting_weeks),
			__("left in the line at {0} cycles", [b.cycles]),
			!b.cutting_weeks ? "--critical"
				: (b.cutting_weeks < 13 ? "--serious" : "--good")));
	}
	tiles.push(tile(__("Demand met"), cov.toFixed(1) + "%",
		__("{0} of {1} stems", [int(dm.planned), int(dm.asked)]),
		cov >= 100 ? "--good" : "--serious"));
	if (dm.at_risk) {
		tiles.push(tile(__("After cuttings short"),
			after == null ? "—" : after.toFixed(1) + "%",
			dm.risk_exceeds_plan
				? __("every stem this plan grows is short of cuttings")
				: __("{0} stems have no cuttings to grow them", [int(dm.at_risk)]),
			"--critical"));
	}
	tiles.push(tile(__("Going in the ground"), int(g.plants),
		__("{0} plantings · {1} beds of {2}", [int(g.plantings), int(g.beds),
			int(g.beds_available)]),
		g.capped ? "--serious" : ""));
	tiles.push(tile(__("First harvest"), w.first_harvest_week || "—",
		w.last_harvest_week && w.last_harvest_week !== w.first_harvest_week
			? __("through {0}", [w.last_harvest_week]) : ""));

	// Order, arrive, plant, cut -- the four dates a cohort has, on one row.
	const rows = (o.arrivals || []).map((a) =>
		`<tr${a.late ? ' style="color:var(--red-600,#c0392b)"' : ""}>` +
		`<td>${esc(a.planting_week || "—")}</td>` +
		`<td>${day(a.order_by)}</td>` +
		`<td>${day(a.arrives)}</td>` +
		`<td>${day(a.planting_date)}</td>` +
		`<td>${esc(a.first_harvest_week || "—")}</td>` +
		`<td class="text-right">${int(a.plants)}</td>` +
		`<td class="text-right">${int(a.beds)}</td>` +
		`<td class="text-right">${a.from_pool ? __("off the pool") : int(a.units)}</td>` +
		`</tr>`).join("");

	el.html(`<style>
.sfp-wrap{margin:0 0 6px}
.sfp-tiles{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:14px}
.sfp-tile{border:1px solid var(--border-color);border-radius:8px;padding:9px 11px;background:var(--card-bg)}
.sfp-tile .k{font-size:.66rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted)}
.sfp-tile .v{font-size:1.18rem;font-weight:600;line-height:1.35;font-variant-numeric:tabular-nums}
.sfp-tile .m{font-size:.72rem;color:var(--text-muted)}
.sfp-basis{font-size:.76rem;color:var(--text-muted);margin:0 0 12px}
table.sfp-t{width:100%;border-collapse:collapse;font-size:.78rem}
table.sfp-t th{text-align:left;font-size:.64rem;text-transform:uppercase;letter-spacing:.5px;
  color:var(--text-muted);padding:5px 8px;border-bottom:1px solid var(--border-color)}
table.sfp-t td{padding:4px 8px;border-bottom:1px solid var(--border-color);
  font-variant-numeric:tabular-nums}
table.sfp-t th.text-right,table.sfp-t td.text-right{text-align:right}
.sfp-scroll{max-height:340px;overflow:auto;border:1px solid var(--border-color);border-radius:8px}
</style>
<div class="sfp-wrap">
  <div class="sfp-tiles">${tiles.join("")}</div>
  ${b.basis ? `<p class="sfp-basis">${esc(b.basis)}</p>` : ""}
  ${dm.risk_note ? `<p class="sfp-basis">${esc(dm.risk_note)}</p>` : ""}
  <div class="sfp-scroll"><table class="sfp-t"><thead><tr>
    <th>${__("Planting week")}</th><th>${__("Order by")}</th><th>${__("Material arrives")}</th>
    <th>${__("Planted")}</th><th>${__("First harvest")}</th>
    <th class="text-right">${__("Plants")}</th><th class="text-right">${__("Beds")}</th>
    <th class="text-right">${__("To buy")}</th>
  </tr></thead><tbody>${rows}</tbody></table></div>
</div>`);
}
