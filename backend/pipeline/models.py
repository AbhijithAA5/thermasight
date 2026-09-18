"""ThermaSight — ML models (numpy).
1) Isolation Forest: unsupervised isolation of structurally unusual rows.
2) Residual MLP: predicts expected energy from operating context; deviations
   of actual energy from the reconstruction are contextual anomalies.
Deterministic seeds for reproducibility."""

from __future__ import annotations

import math

import numpy as np


def mulberry32(seed: int):
    a = seed & 0xFFFFFFFF

    def rng() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = ((t ^ (t >> 15)) * (1 + t)) & 0xFFFFFFFF
        t = ((t ^ (t >> 7)) * (61 + t)) & 0xFFFFFFFF
        t = (t ^ (t >> 14)) & 0xFFFFFFFF
        return t / 4294967296.0

    return rng


def gauss(rng) -> float:
    u = 0.0
    while u == 0.0:
        u = rng()
    v = rng()
    return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v)


class IsolationForest:
    """Same algorithm as the reference TypeScript implementation: per-tree
    random split between min/max of a random feature, depth-capped."""

    def __init__(self) -> None:
        self.trees: list[list[dict]] = []
        self.sample_size = 256
        self.limit = 0
        self.d = 0
        self.n = 0

    @staticmethod
    def _c(n: float) -> float:
        if n <= 1:
            return 1.0
        return 2.0 * (math.log(n - 1) + 0.5772156649) - (2.0 * (n - 1)) / n

    def fit(self, X: np.ndarray, n_trees: int = 80, max_samples: int = 256, seed: int = 7) -> None:
        self.n = X.shape[0]
        self.sample_size = min(max_samples, max(2, self.n))
        self.limit = math.ceil(math.log2(max(2, self.sample_size))) + 2
        self.d = X.shape[1]
        rng = mulberry32(seed)
        universe = np.arange(self.n)
        self.trees = []
        for _ in range(n_trees):
            idxs = np.array(
                [universe[int(rng() * len(universe))] for _ in range(self.sample_size)], dtype=int
            )
            nodes: list[dict] = []
            self._build(X, idxs, 0, nodes, rng)
            self.trees.append(nodes)

    def _build(self, X: np.ndarray, idxs: np.ndarray, depth: int, nodes: list[dict], rng) -> int:
        if depth >= self.limit or len(idxs) <= 1:
            nodes.append({"size": len(idxs)})
            return len(nodes) - 1
        f = int(rng() * self.d)
        col = X[idxs, f]
        lo = float(col.min())
        hi = float(col.max())
        if hi - lo < 1e-12:
            nodes.append({"size": len(idxs)})
            return len(nodes) - 1
        left = np.empty(0, dtype=int)
        right = np.empty(0, dtype=int)
        thr = 0.0
        for _ in range(8):
            thr = lo + rng() * (hi - lo)
            left = idxs[col < thr]
            right = idxs[col >= thr]
            if len(left) > 0 and len(right) > 0:
                break
        if len(left) == 0 or len(right) == 0:
            nodes.append({"size": len(idxs)})
            return len(nodes) - 1
        node_idx = len(nodes)
        nodes.append({"feat": f, "thr": thr, "left": -1, "right": -1})
        l = self._build(X, left, depth + 1, nodes, rng)
        r = self._build(X, right, depth + 1, nodes, rng)
        nodes[node_idx]["left"] = l
        nodes[node_idx]["right"] = r
        return node_idx

    def _path(self, nodes: list[dict], x: np.ndarray) -> float:
        idx = 0
        depth = 0
        while True:
            nd = nodes[idx]
            if "size" in nd:
                return depth + self._c(nd["size"])
            depth += 1
            idx = nd["left"] if x[nd["feat"]] < nd["thr"] else nd["right"]

    def score(self, X: np.ndarray) -> np.ndarray:
        out = np.zeros(X.shape[0], dtype=float)
        if not self.trees:
            return out
        c = self._c(self.sample_size)
        for i in range(X.shape[0]):
            total = 0.0
            for nodes in self.trees:
                total += self._path(nodes, X[i])
            avg = total / len(self.trees)
            out[i] = math.pow(2.0, -avg / c)
        return out


class MLPRegressor:
    """1-hidden-layer network, momentum SGD with gradient clipping and early
    stopping — mirrors the reference TypeScript implementation."""

    def __init__(self, hidden: int = 24) -> None:
        self.hidden = hidden
        self.W1: np.ndarray | None = None
        self.b1: np.ndarray | None = None
        self.W2: np.ndarray | None = None
        self.b2 = 0.0

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        epochs: int = 150,
        lr: float = 0.02,
        momentum: float = 0.9,
        batch: int = 48,
        seed: int = 11,
        patience: int = 22,
    ) -> dict:
        n, d = X.shape
        h = self.hidden
        rng = mulberry32(seed)

        W1 = (np.random.default_rng(seed).random((d, h)) * 2 - 1) * math.sqrt(2 / d) * 0.5
        b1 = np.zeros(h)
        W2 = (np.random.default_rng(seed + 1).random(h) * 2 - 1) * math.sqrt(2 / h) * 0.5
        b2 = 0.0

        order = np.arange(n)
        np.random.default_rng(seed + 2).shuffle(order)
        val_count = max(1, int(n * 0.1))
        train_idx = order[: n - val_count]
        val_idx = order[n - val_count:]

        best_val = float("inf")
        best = (W1.copy(), b1.copy(), W2.copy(), b2)
        bad_epochs = 0
        done_epochs = 0

        for ep in range(epochs):
            perm = train_idx.copy()
            np.random.default_rng(seed + 3 + ep).shuffle(perm)
            for b in range(0, len(perm), batch):
                rows = perm[b : b + batch]
                Xb = X[rows]
                yb = y[rows]
                z1 = Xb @ W1 + b1
                a1 = np.maximum(z1, 0.0)
                out = a1 @ W2 + b2
                err = out - yb
                gW2 = (a1.T @ err) / len(rows)
                gb2 = float(err.sum()) / len(rows)
                g_a1 = err[:, None] * W2[None, :]
                g_z1 = g_a1 * (z1 > 0)
                gW1 = (Xb.T @ g_z1) / len(rows)
                gb1 = g_z1.sum(axis=0) / len(rows)
                # gradient clipping
                max_abs = max(float(np.abs(gb2)), float(np.abs(gW2).max()), float(np.abs(gb1).max()), float(np.abs(gW1).max()))
                clip = min(1.0, 40.0 / max_abs) if max_abs > 40 else 1.0
                gW1 *= clip
                gb1 *= clip
                gW2 *= clip
                gb2 *= clip
                # momentum (velocities kept implicitly per iteration via direct step)
                if not hasattr(self, "_v"):
                    self._v = {
                        "W1": np.zeros_like(W1), "b1": np.zeros_like(b1),
                        "W2": np.zeros_like(W2), "b2": 0.0,
                    }
                v = self._v
                v["W1"] = momentum * v["W1"] + lr * gW1
                W1 -= v["W1"]
                v["b1"] = momentum * v["b1"] + lr * gb1
                b1 -= v["b1"]
                v["W2"] = momentum * v["W2"] + lr * gW2
                W2 -= v["W2"]
                v["b2"] = momentum * v["b2"] + lr * gb2
                b2 -= v["b2"]

            def mse(rows: np.ndarray) -> float:
                preds = np.maximum(X[rows] @ W1 + b1, 0.0) @ W2 + b2
                return float(np.mean((preds - y[rows]) ** 2))

            vl = mse(val_idx)
            done_epochs = ep + 1
            if vl < best_val - 1e-4:
                best_val = vl
                bad_epochs = 0
                best = (W1.copy(), b1.copy(), W2.copy(), b2)
            else:
                bad_epochs += 1
                if bad_epochs >= patience:
                    break

        self.W1, self.b1, self.W2, self.b2 = best
        self._v = None
        return {"trainLoss": mse(train_idx), "valLoss": best_val, "epochs": done_epochs}

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.W1 is None:
            return np.zeros(X.shape[0])
        return np.maximum(X @ self.W1 + self.b1, 0.0) @ self.W2 + self.b2


def spearman(xs, ys) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0

    def rank(a) -> np.ndarray:
        a = np.asarray(a, dtype=float)
        order = np.argsort(a, kind="mergesort", axis=0)
        r = np.empty(n)
        i = 0
        while i < n:
            j = i
            while j + 1 < n and a[order[j + 1]] == a[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            r[order[i : j + 1]] = avg
            i = j + 1
        return r

    rx = rank(xs)
    ry = rank(ys)
    mx = rx.mean()
    my = ry.mean()
    num = float(((rx - mx) * (ry - my)).sum())
    den = float(np.sqrt(((rx - mx) ** 2).sum() * ((ry - my) ** 2).sum()))
    return num / den if den else 0.0


def quantile_sorted(arr: np.ndarray, q: float) -> float:
    if arr.size == 0:
        return 0.0
    s = np.sort(arr)
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))