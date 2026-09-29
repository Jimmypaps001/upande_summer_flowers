"""Render summer_flowers_navigation.html from one table of tiles.

The packhouse block this copies was hand-written HTML, which is how it ended up
with tiles pointing at routes that no longer exist. Here the tiles are data, the
markup is generated, and build_nav.py checks every route against the site before
it writes the block.
"""

import os

ICONS = {
	"gauge": '<path d="M12 21a9 9 0 1 0-9-9"/><path d="M3 12h2M12 3v2M20.5 7.5 19 9"/><path d="m12 12 5-3"/>',
	"layers": '<path d="m12 2 9 5-9 5-9-5 9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/>',
	"grid": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
	"route": '<circle cx="6" cy="19" r="3"/><circle cx="18" cy="5" r="3"/><path d="M9 19h6a4 4 0 0 0 4-4V8"/>',
	"trend": '<polyline points="3 17 9 11 13 15 21 7"/><polyline points="15 7 21 7 21 13"/>',
	"clipboard": '<rect x="8" y="3" width="8" height="4" rx="1"/><path d="M16 5h2a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="8" y1="16" x2="13" y2="16"/>',
	"coins": '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
	"calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/>',
	"cart": '<path d="M6 6h15l-1.5 9h-12z"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/><path d="M6 6 5 3H2"/>',
	"sprout": '<path d="M12 22V10"/><path d="M12 12C12 8 9 5 4 5c0 5 3 7 8 7z"/><path d="M12 14c0-3.5 2.5-6 7-6 0 4.5-2.5 6-7 6z"/>',
	"mother": '<circle cx="12" cy="7" r="3"/><path d="M12 10v11"/><path d="M12 14 7 18M12 14l5 4"/>',
	"tray": '<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9" y1="4" x2="9" y2="20"/><line x1="15" y1="4" x2="15" y2="20"/><line x1="3" y1="12" x2="21" y2="12"/>',
	"box": '<path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/>',
	"map": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
	"rows": '<rect x="3" y="4" width="18" height="16" rx="1"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="3" y1="15" x2="21" y2="15"/>',
	"swap": '<path d="M7 4 3 8l4 4"/><path d="M3 8h13a4 4 0 0 1 0 8h-1"/><path d="m17 20 4-4-4-4"/>',
	"tools": '<path d="M14.7 6.3a4 4 0 0 0 5 5L21 21H3l6-9"/><circle cx="8" cy="6" r="3"/>',
	"leaf": '<path d="M11 20A7 7 0 0 1 4 13c0-6 7-9 16-9 0 9-4 15-9 16z"/><path d="M4 21c3-6 7-9 11-11"/>',
	"book": '<path d="M4 4h11a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3z"/><path d="M18 7h2v13H7"/>',
	"stack": '<rect x="4" y="3" width="16" height="5" rx="1"/><rect x="4" y="10" width="16" height="5" rx="1"/><rect x="4" y="17" width="16" height="4" rx="1"/>',
	"star": '<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>',
	"cog": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 2.6 7"/>',
}

# hue -> (tint background, ink)
HUES = {
	"blue": ("rgba(59,130,246,.13)", "#3b82f6"),
	"violet": ("rgba(139,92,246,.13)", "#7c53e0"),
	"green": ("rgba(34,197,94,.13)", "#16a34a"),
	"emerald": ("rgba(16,185,129,.13)", "#059669"),
	"cyan": ("rgba(6,182,212,.13)", "#0891b2"),
	"amber": ("rgba(245,158,11,.13)", "#d97706"),
	"slate": ("rgba(100,116,139,.13)", "#64748b"),
}

HERO = ("Summer Flowers", "demand &middot; procure &middot; propagate &middot; plant &middot; cut")

# (section, hue, [(label, sub, href, roles, count_doctype, filters, warn)])
SECTIONS = [
	("Start here", "blue", [
		("Planning Dashboard", "Demand, plans, approvals", "/summer-flowers-planning", "", None, None, False),
		("Season Overview", "Where every variety stands", "/summer-flowers-overview", "", None, None, False),
		("Crop Cycle Board", "What is in the ground", "/crop-cycles", "", None, None, False),
		("How Planning Works", "The chain, end to end", "/summer-flowers-flow", "", None, None, False),
	]),
	("Demand and plan", "violet", [
		("Market Demand", "Stems the market wants", "/app/summer-flower-market-demand", "", "Summer Flower Market Demand", {}, False),
		("Production Plan", "Plants, beds and weeks", "/app/summer-flower-production-plan", "", "Summer Flower Production Plan", {}, False),
		("Awaiting Approval", "Waiting on a manager", "/app/summer-flower-production-plan?workflow_state=Pending%20Approval", "", "Summer Flower Production Plan", {"workflow_state": "Pending Approval"}, True),
		("Season Plan", "Receive, plant, deliver", "/app/summer-flower-season-plan", "", "Summer Flower Season Plan", {}, False),
		("Revenue Budget", "What the season earns", "/app/summer-flower-budget", "", "Summer Flower Budget", {}, False),
	]),
	("Buy and propagate", "green", [
		("Procurement Plan", "What to buy, and when", "/app/summer-flower-procurement-plan", "", "Summer Flower Procurement Plan", {}, False),
		("Propagation Plan", "Arrival and delivery weeks", "/app/summer-flower-propagation-plan", "", "Summer Flower Propagation Plan", {}, False),
		("Motherstock Batches", "Mothers standing, and cuts", "/app/summer-flower-motherstock-batch", "", "Summer Flower Motherstock Batch", {}, False),
		("Seedling Requests", "What the nursery raises", "/app/seedling-request", "", "Seedling Request", {}, False),
		("Propagation Batches", "Trays on the bench", "/app/propagation-batch", "", "Propagation Batch", {}, False),
	]),
	("Plant and grow", "emerald", [
		("Planting Calendar", "Which bed, which week", "/app/planting-calendar", "", "Planting Calendar", {}, False),
		("Crop Cycles", "Standing crops", "/app/crop-cycle", "", None, None, False),
		("Bed Work Orders", "Prepare, plant, uproot", "/app/bed-work-order", "", "Bed Work Order", {"status": ["in", ["Not Started", "In Progress", "Paused"]]}, False),
	]),
	("Blocks and beds", "cyan", [
		("Blocks", "Growing blocks and beds", "/app/block", "", "Block", {}, False),
		("Beds", "Every bed on the farm", "/app/bed", "", None, None, False),
		("Block Changes", "Split, merge or retire", "/app/block-change-request", "", "Block Change Request", {}, False),
	]),
	("Cost and inputs", "amber", [
		("Block Budget", "Cost per block, per season", "/app/block-production-budget", "", "Block Production Budget", {}, False),
		("Input Requests", "Fertiliser and chemicals", "/app/material-request", "", None, None, False),
		("Biological Assets", "Standing crops on the books", "/app/asset?asset_category=BIOLOGICAL%20ASSETS%20(PLANTS)", "", None, None, False),
	]),
	("Protocol and setup", "slate", [
		("Crop Protocol", "Weeks, yields and losses", "/app/crop-protocol", "", None, None, False),
		("Protocol Versions", "What each approval changed", "/app/crop-protocol-version", "", None, None, False),
		("Grades", "Grades and stem counts", "/app/summer-flower-grade", "System Manager,Agriculture Manager,Farm Manager", None, None, False),
		("Settings", "Module-wide defaults", "/app/summer-flower-settings", "System Manager,Agriculture Manager", None, None, False),
	]),
]

ICON_FOR = {
	"Planning Dashboard": "gauge", "Season Overview": "layers", "Crop Cycle Board": "grid",
	"How Planning Works": "route", "Market Demand": "trend", "Production Plan": "clipboard",
	"Awaiting Approval": "star", "Season Plan": "calendar", "Revenue Budget": "coins",
	"Procurement Plan": "cart", "Propagation Plan": "sprout", "Motherstock Batches": "mother",
	"Seedling Requests": "tray", "Propagation Batches": "box", "Planting Calendar": "calendar",
	"Crop Cycles": "leaf", "Bed Work Orders": "tools", "Blocks": "map", "Beds": "rows",
	"Block Changes": "swap", "Block Budget": "coins", "Input Requests": "stack",
	"Biological Assets": "leaf", "Crop Protocol": "book", "Protocol Versions": "stack",
	"Grades": "star", "Settings": "cog",
}

SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
       'stroke-linecap="round" stroke-linejoin="round">%s</svg>')


def render():
	import json as _json
	out = ['<div class="sfn">',
	       '  <div class="sfn-hero"><div class="sfn-hero-tt">%s</div>'
	       '<div class="sfn-hero-sub">%s</div></div>' % HERO]
	for title, hue, tiles in SECTIONS:
		bg, ink = HUES[hue]
		out.append('  <div class="sfn-title">%s</div>' % title)
		out.append('  <div class="sfn-grid">')
		for label, sub, href, roles, count_dt, filters, warn in tiles:
			attrs = ['class="sfn-tile"', 'data-roles="%s"' % roles, 'href="%s"' % href]
			if count_dt:
				attrs.append('data-count="%s"' % count_dt)
				attrs.append("data-filters='%s'" % _json.dumps(filters or {}))
			if warn:
				attrs.append("data-warn")
			icon = SVG % ICONS[ICON_FOR[label]]
			out.append('    <a %s>' % " ".join(attrs))
			out.append('      <span class="sfn-ic" style="background:%s;color:%s">%s</span>'
			           % (bg, ink, icon))
			out.append('      <span class="sfn-tx"><span class="sfn-lb">%s</span>'
			           '<span class="sfn-sub">%s</span></span>' % (label, sub))
			out.append('    </a>')
		out.append('  </div>')
	out.append('</div>')
	return "\n".join(out) + "\n"


def routes():
	"""Every href the block points at, for build_nav.py to check."""
	return [(label, href) for _, _, tiles in SECTIONS for label, _, href, _, _, _, _ in tiles]


def counts():
	return [(label, dt, f) for _, _, tiles in SECTIONS
	        for label, _, _, _, dt, f, _ in tiles if dt]


if __name__ == "__main__":
	here = os.path.dirname(os.path.abspath(__file__))
	with open(os.path.join(here, "summer_flowers_navigation.html"), "w") as fh:
		fh.write(render())
	print("wrote summer_flowers_navigation.html (%d tiles)" % len(routes()))
