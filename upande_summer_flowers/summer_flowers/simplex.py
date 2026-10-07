# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

"""A small linear programme, in plain Python.

Written out longhand rather than imported. The planner has to run wherever the
app is installed, and scipy is on this bench only because another app happened
to pull it in -- a plan that quietly comes out at 190% of demand on one site and
100% on another, because of a package nobody declared, is worse than no solver.

Two-phase simplex on a dense tableau, which is the textbook method and is
enough: the planning problem is a hundred-odd rows and a hundred-odd columns,
and it is solved once when a plan is generated.
"""

EPS = 1e-9


def minimise(c, a_ub, b_ub, max_iter=20_000):
	"""min c·x subject to a_ub·x <= b_ub, x >= 0. Returns x, or None if infeasible.

	No bounds other than non-negativity, because nothing here needs them: a
	cohort is a number of plants and cannot be negative, and every ceiling is
	written as a row.
	"""
	m, n = len(a_ub), len(c)
	if not m:
		return [0.0] * n

	# Normalise to non-negative right-hand sides. A row with a negative one is
	# multiplied through, which turns it from <= into >=, so it gets a surplus
	# column and an artificial to start the basis off.
	rows, rhs, ge = [], [], []
	for i in range(m):
		if b_ub[i] < 0:
			rows.append([-v for v in a_ub[i]])
			rhs.append(-b_ub[i])
			ge.append(True)
		else:
			rows.append(list(a_ub[i]))
			rhs.append(float(b_ub[i]))
			ge.append(False)

	n_art = sum(1 for g in ge if g)
	width = n + m + n_art + 1          # structural | slack/surplus | artificial | rhs
	art_at = n + m
	tab = []
	basis = []
	k = 0
	for i in range(m):
		row = rows[i] + [0.0] * (m + n_art + 1)
		row[n + i] = -1.0 if ge[i] else 1.0
		row[-1] = rhs[i]
		if ge[i]:
			row[art_at + k] = 1.0
			basis.append(art_at + k)
			k += 1
		else:
			basis.append(n + i)
		tab.append(row)

	def pivot(pr, pc):
		prow = tab[pr]
		pv = prow[pc]
		inv = 1.0 / pv
		for j in range(width):
			prow[j] *= inv
		for r in range(m):
			if r == pr:
				continue
			row = tab[r]
			f = row[pc]
			if f == 0.0:
				continue
			for j in range(width):
				if prow[j] != 0.0:
					row[j] -= f * prow[j]
		basis[pr] = pc

	def run(cost, allowed, iters):
		"""Dantzig's rule, falling back to Bland's if it stops making progress."""
		bland = False
		stall = 0
		last = None
		for _ in range(iters):
			# reduced costs, z_j - c_j, computed from the basis
			dual = [cost[basis[r]] for r in range(m)]
			best_j, best_v = -1, -EPS
			for j in allowed:
				if j in basis:
					continue
				acc = 0.0
				for r in range(m):
					if dual[r]:
						acc += dual[r] * tab[r][j]
				red = cost[j] - acc
				if red < best_v:
					best_j, best_v = j, red
					if bland:
						break
			if best_j < 0:
				return True
			# ratio test
			pr, best_ratio = -1, None
			for r in range(m):
				v = tab[r][best_j]
				if v > EPS:
					ratio = tab[r][-1] / v
					if best_ratio is None or ratio < best_ratio - EPS or (
							abs(ratio - best_ratio) <= EPS and basis[r] < basis[pr]):
						pr, best_ratio = r, ratio
			if pr < 0:
				return True  # unbounded; cannot happen with a cost floor of zero
			pivot(pr, best_j)
			obj = sum(cost[basis[r]] * tab[r][-1] for r in range(m))
			if last is not None and abs(obj - last) < EPS:
				stall += 1
				if stall > 20:
					bland = True
			else:
				stall = 0
			last = obj
		return False

	# ---- phase one: can every row be satisfied at all
	if n_art:
		cost = [0.0] * width
		for j in range(art_at, art_at + n_art):
			cost[j] = 1.0
		run(cost, range(art_at + n_art), max_iter)
		if sum(tab[r][-1] for r in range(m) if basis[r] >= art_at) > 1e-6:
			return None
		# An artificial left in the basis at zero is a redundant row; pivot it
		# out on anything real, or leave it pinned at zero.
		for r in range(m):
			if basis[r] >= art_at:
				for j in range(n + m):
					if abs(tab[r][j]) > EPS:
						pivot(r, j)
						break

	# ---- phase two: the objective that was asked for
	cost = list(c) + [0.0] * (m + n_art + 1)
	run(cost, range(n + m), max_iter)

	x = [0.0] * n
	for r in range(m):
		if basis[r] < n:
			x[basis[r]] = tab[r][-1]
	return x
