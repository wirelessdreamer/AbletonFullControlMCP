"""2-operator FM — simplified Operator config.

Carrier sine modulated by a single modulator sine. Modulator's amplitude
follows its own ADSR so we get FM "pluck" character. Carrier amplitude
follows the main amp ADSR.
"""

from __future__ import annotations

import numpy as np

from .base import BenchSynthRenderer, adsr_envelope


class FM2OpRenderer(BenchSynthRenderer):
    """Carrier + modulator FM synth (one operator pair)."""

    PARAM_RANGES = {
        "freq": (40.0, 1500.0),
        "mod_ratio": (0.25, 8.0),       # modulator freq / carrier freq
        "mod_index": (0.0, 12.0),       # FM depth
        "mod_attack": (0.001, 0.5),
        "mod_decay": (0.001, 1.0),
        "amp_attack": (0.001, 1.0),
        "amp_decay": (0.01, 1.0),
        "amp_sustain": (0.0, 1.0),
        "amp_release": (0.01, 1.0),
        "feedback": (0.0, 0.9),         # modulator self-feedback (rough)
    }
    PARAM_DEFAULTS = {
        "freq": 220.0,
        "mod_ratio": 1.0,
        "mod_index": 2.0,
        "mod_attack": 0.005,
        "mod_decay": 0.4,
        "amp_attack": 0.01,
        "amp_decay": 0.4,
        "amp_sustain": 0.6,
        "amp_release": 0.3,
        "feedback": 0.0,
    }

    def _render(self, p: dict[str, float]) -> np.ndarray:
        sr = self.sample_rate
        n = self._n_samples()
        t = self._time_axis()

        # Modulator amplitude has its own AD envelope (sustain=0, release=tail).
        mod_env = adsr_envelope(
            n, sr, p["mod_attack"], p["mod_decay"], 0.0, max(p["mod_decay"] * 0.2, 0.01)
        )
        amp_env = adsr_envelope(
            n, sr, p["amp_attack"], p["amp_decay"], p["amp_sustain"], p["amp_release"]
        )

        f_c = float(p["freq"])
        f_m = f_c * float(p["mod_ratio"])

        # Modulator with simple feedback (one-sample delayed self-mod approximated by static fb factor).
        fb = float(np.clip(p["feedback"], 0.0, 0.95))
        mod_phase = 2.0 * np.pi * f_m * t
        # Feedback approximation: scale modulator output by (1 + fb * prev) — emulate brightness.
        modulator = np.sin(mod_phase).astype(np.float32)
        if fb > 0.0:
            # Iterative refinement (cheap) for self-modulation.
            for _ in range(2):
                modulator = np.sin(mod_phase + fb * modulator).astype(np.float32)
        modulator *= mod_env * float(p["mod_index"])

        carrier = np.sin(2.0 * np.pi * f_c * t + modulator).astype(np.float32)
        return (carrier * amp_env).astype(np.float32)
