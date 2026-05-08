"""4-operator FM with selectable algorithm — Operator-style.

Each operator is a sine with its own ratio/index/AD envelope. Algorithms
1..7 wire the four operators together. We pick a small but distinct set
covering pure FM stacks, parallel mixes, and combinations so different
algorithms produce clearly different timbres.

Algorithm map (carriers go to output):
  1: 4 → 3 → 2 → 1                        (single chain, op1 carrier)
  2: 4 → 3, 4 → 2, 3 → 1                  (branched chain)
  3: (4 → 3) + 2 + 1                      (op3 modulates op? Actually: 4→3 carrier, plus 2 and 1)
     concretely: out = op1 + op2 + op3(modded by 4)
  4: 4 → 3, 2 → 1, out = op1 + op3        (two parallel pairs)
  5: 4 → (1, 2, 3), out = op1+op2+op3     (1 mod fans out)
  6: out = op1 + op2 + op3 + op4          (four parallel sines — additive)
  7: 4 → 1, 3 → 1, 2 → 1                  (three modulators on one carrier)
"""

from __future__ import annotations

import numpy as np

from .base import BenchSynthRenderer, adsr_envelope


class FM4OpRenderer(BenchSynthRenderer):
    """4-operator FM synth with 7 algorithm wirings."""

    PARAM_RANGES = {
        "freq": (40.0, 1500.0),
        "algorithm": (1.0, 7.0),
        "op1_ratio": (0.25, 8.0),
        "op2_ratio": (0.25, 8.0),
        "op3_ratio": (0.25, 8.0),
        "op4_ratio": (0.25, 8.0),
        "op1_index": (0.0, 8.0),
        "op2_index": (0.0, 8.0),
        "op3_index": (0.0, 8.0),
        "op4_index": (0.0, 8.0),
        "op1_decay": (0.05, 1.5),
        "op2_decay": (0.05, 1.5),
        "op3_decay": (0.05, 1.5),
        "op4_decay": (0.05, 1.5),
    }
    PARAM_DEFAULTS = {
        "freq": 220.0,
        "algorithm": 1.0,
        "op1_ratio": 1.0,
        "op2_ratio": 2.0,
        "op3_ratio": 3.0,
        "op4_ratio": 4.0,
        "op1_index": 1.0,
        "op2_index": 1.5,
        "op3_index": 1.5,
        "op4_index": 1.5,
        "op1_decay": 0.6,
        "op2_decay": 0.5,
        "op3_decay": 0.4,
        "op4_decay": 0.3,
    }

    def _operator(
        self,
        n: int,
        sr: int,
        f: float,
        index: float,
        decay: float,
        modulator: np.ndarray | None = None,
        as_carrier: bool = False,
    ) -> np.ndarray:
        """One operator. ``as_carrier=True`` uses a sustained envelope so the
        algorithm wiring matters: in alg1 only op1 is a carrier, in alg6 all
        four are carriers, etc. — that produces clearly different spectra."""
        t = np.arange(n, dtype=np.float32) / float(sr)
        if as_carrier:
            # Sustained AD with a soft body — the audible character.
            env = adsr_envelope(n, sr, 0.01, decay, 0.7, max(decay * 0.5, 0.05))
            scale = 1.0
        else:
            # Modulator: plucky AD with no sustain. Index controls FM depth.
            env = adsr_envelope(n, sr, 0.005, decay, 0.0, max(decay * 0.2, 0.01))
            scale = float(index)
        phase = 2.0 * np.pi * f * t
        if modulator is not None:
            phase = phase + modulator
        sig = np.sin(phase).astype(np.float32) * env
        return (sig * scale).astype(np.float32)

    def _render(self, p: dict[str, float]) -> np.ndarray:
        sr = self.sample_rate
        n = self._n_samples()
        f0 = float(p["freq"])
        algo = int(round(np.clip(p["algorithm"], 1, 7)))

        # Pre-compute frequencies & per-op metadata.
        ratios = [p["op1_ratio"], p["op2_ratio"], p["op3_ratio"], p["op4_ratio"]]
        indices = [p["op1_index"], p["op2_index"], p["op3_index"], p["op4_index"]]
        decays = [p["op1_decay"], p["op2_decay"], p["op3_decay"], p["op4_decay"]]
        freqs = [f0 * float(r) for r in ratios]

        def op(
            i: int,
            modulator: np.ndarray | None = None,
            as_carrier: bool = False,
        ) -> np.ndarray:
            return self._operator(
                n, sr, freqs[i], indices[i], decays[i], modulator, as_carrier=as_carrier
            )

        # Carriers we mix into the output for each algorithm.
        if algo == 1:
            # 4 → 3 → 2 → 1 (op1 is the only carrier).
            m4 = op(3)
            m3 = op(2, m4)
            m2 = op(1, m3)
            out = op(0, m2, as_carrier=True)
        elif algo == 2:
            # 4 → 3, 4 → 2, 3 → 1, carriers: op1 + op2
            m4 = op(3)
            m3 = op(2, m4)
            c2 = op(1, m4, as_carrier=True)
            c1 = op(0, m3, as_carrier=True)
            out = c1 + c2
        elif algo == 3:
            # 4 → 3 carrier, plus parallel 1, 2
            m4 = op(3)
            c3 = op(2, m4, as_carrier=True)
            c1 = op(0, as_carrier=True)
            c2 = op(1, as_carrier=True)
            out = c1 + c2 + c3
        elif algo == 4:
            # 4 → 3, 2 → 1, carriers: op1 + op3
            m4 = op(3)
            c3 = op(2, m4, as_carrier=True)
            m2 = op(1)
            c1 = op(0, m2, as_carrier=True)
            out = c1 + c3
        elif algo == 5:
            # 4 fans out to 1, 2, 3 — three carriers, all modulated by op4
            m4 = op(3)
            c1 = op(0, m4, as_carrier=True)
            c2 = op(1, m4, as_carrier=True)
            c3 = op(2, m4, as_carrier=True)
            out = c1 + c2 + c3
        elif algo == 6:
            # All parallel — four sustained sines, additive
            out = (
                op(0, as_carrier=True)
                + op(1, as_carrier=True)
                + op(2, as_carrier=True)
                + op(3, as_carrier=True)
            )
        else:  # algo == 7
            # 4 → 1, 3 → 1, 2 → 1 — three modulators stack on op1
            stacked = op(1) + op(2) + op(3)
            out = op(0, stacked, as_carrier=True)
        return out.astype(np.float32)
