"""Sweep planner — generate parameter cells to probe.

Three strategies:

- ``grid``: full Cartesian product, ``steps_per_param ** n_params`` cells.
- ``lhs``: Latin Hypercube, fixed total of ``steps_per_param * n_params`` cells
  (one fully space-filling design — much cheaper than the grid for >2 params).
- ``random``: uniform random sampling, same total as LHS.

The planner is deterministic given ``seed``. A planner instance is iterable
and ``len()``-able, so callers can drive a progress bar without enumerating.
"""

from __future__ import annotations

from typing import Iterator, Mapping, Sequence

import numpy as np

ParamRanges = Mapping[str, tuple[float, float]]


class SweepPlanner:
    """Yield parameter cells for a probe sweep.

    Args:
        params: mapping of param_name → (min, max).
        steps_per_param: grid resolution per axis (``grid``) OR multiplier
            for total samples (``lhs``, ``random`` use ``steps_per_param * n_params``).
        strategy: 'grid' | 'lhs' | 'random'.
        seed: RNG seed for ``lhs`` and ``random``.
    """

    def __init__(
        self,
        params: ParamRanges,
        steps_per_param: int = 5,
        strategy: str = "grid",
        seed: int | None = 0,
    ) -> None:
        if strategy not in {"grid", "lhs", "random"}:
            raise ValueError(f"unknown strategy: {strategy!r}")
        if steps_per_param < 1:
            raise ValueError("steps_per_param must be >= 1")
        if not params:
            raise ValueError("params must not be empty")

        self.params: dict[str, tuple[float, float]] = {
            name: (float(lo), float(hi)) for name, (lo, hi) in params.items()
        }
        self.steps_per_param = int(steps_per_param)
        self.strategy = strategy
        self.seed = seed
        self._names: tuple[str, ...] = tuple(self.params.keys())
        self._lows = np.array([self.params[n][0] for n in self._names], dtype=np.float64)
        self._highs = np.array([self.params[n][1] for n in self._names], dtype=np.float64)

    @property
    def param_names(self) -> Sequence[str]:
        return self._names

    def __len__(self) -> int:
        n_params = len(self._names)
        if self.strategy == "grid":
            return self.steps_per_param ** n_params
        return self.steps_per_param * n_params

    def __iter__(self) -> Iterator[dict[str, float]]:
        if self.strategy == "grid":
            yield from self._grid()
        elif self.strategy == "lhs":
            yield from self._lhs()
        else:
            yield from self._random()

    def _grid(self) -> Iterator[dict[str, float]]:
        axes = [
            np.linspace(self._lows[i], self._highs[i], self.steps_per_param)
            for i in range(len(self._names))
        ]
        # itertools.product would also work; np.meshgrid keeps everything in
        # numpy land for symmetry with the other strategies.
        mesh = np.meshgrid(*axes, indexing="ij")
        flat = np.stack([m.ravel() for m in mesh], axis=1)  # (N, n_params)
        for row in flat:
            yield {name: float(row[i]) for i, name in enumerate(self._names)}

    def _lhs(self) -> Iterator[dict[str, float]]:
        from scipy.stats import qmc

        n = self.steps_per_param * len(self._names)
        sampler = qmc.LatinHypercube(d=len(self._names), seed=self.seed)
        unit = sampler.random(n=n)  # (n, d) in [0, 1)
        scaled = qmc.scale(unit, self._lows, self._highs)
        for row in scaled:
            yield {name: float(row[i]) for i, name in enumerate(self._names)}

    def _random(self) -> Iterator[dict[str, float]]:
        n = self.steps_per_param * len(self._names)
        rng = np.random.default_rng(self.seed)
        unit = rng.random(size=(n, len(self._names)))
        scaled = self._lows + unit * (self._highs - self._lows)
        for row in scaled:
            yield {name: float(row[i]) for i, name in enumerate(self._names)}

    def explain_axis(self, name: str, steps: int = 11, fixed: Mapping[str, float] | None = None) -> Iterator[dict[str, float]]:
        """Sweep a single param across its range with the others held fixed.

        Used by ``sound_explain_parameter``. ``fixed`` defaults to the midpoint
        of every other param's range.
        """
        if name not in self.params:
            raise KeyError(name)
        steps = max(2, int(steps))
        lo, hi = self.params[name]
        base: dict[str, float] = {}
        for k, (low, high) in self.params.items():
            base[k] = 0.5 * (low + high)
        if fixed:
            base.update({k: float(v) for k, v in fixed.items() if k in self.params})
        for v in np.linspace(lo, hi, steps):
            cell = dict(base)
            cell[name] = float(v)
            yield cell
