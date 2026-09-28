# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Calibration — grounding threshold (US-076 phase 2).

Loads the synthetic, secret-free calibration set and the production bge-m3 model,
scores every labelled claim against its context (same cosine logic as
``answer_validator.evaluate_grounding``, applied per-claim so short CJK sentences
are measured too), and reports where to set ``GROUNDING_THRESHOLD``:
- the score distribution per label (supported / offtopic / contradiction);
- a threshold sweep on supported vs offtopic (what the deterministic check CAN
  separate) — false positives (a supported claim flagged) are the worst case if
  the check ever runs default-on;
- how many 'contradiction' claims slip through (they score high — the
  deterministic blind spot that motivates the phase-2 LLM pass).

Run: python scripts/calibrate_grounding_threshold.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
_DATASET = _ROOT / "tests/fixtures/grounding_calibration.json"


def _load_model():
    from sentence_transformers import SentenceTransformer

    print("Loading bge-m3 …", flush=True)
    return SentenceTransformer("BAAI/bge-m3")


def _score(model, claim: str, contexts: list[str]) -> float:
    """Max cosine similarity claim<->context (mirrors evaluate_grounding)."""
    import numpy as np

    def _l2(m):
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return m / n

    cv = _l2(np.asarray(model.encode([claim], convert_to_numpy=True), dtype=float))
    xv = _l2(np.asarray(model.encode(contexts, convert_to_numpy=True), dtype=float))
    return float((cv @ xv.T).max())


def main():
    data = json.loads(_DATASET.read_text(encoding="utf-8"))
    model = _load_model()

    by_label: dict[str, list[float]] = {"supported": [], "offtopic": [], "contradiction": []}
    for case in data["cases"]:
        for claim in case["claims"]:
            by_label[claim["label"]].append(_score(model, claim["text"], case["context"]))

    print("\n=== Score d'ancrage par catégorie ===")
    print(f"{'label':<14}{'n':>3}{'min':>8}{'médiane':>9}{'max':>8}{'moyenne':>9}")
    for label in ("supported", "offtopic", "contradiction"):
        v = by_label[label]
        if v:
            print(f"{label:<14}{len(v):>3}{min(v):>8.3f}"
                  f"{statistics.median(v):>9.3f}{max(v):>8.3f}{statistics.mean(v):>9.3f}")

    sup, off, con = by_label["supported"], by_label["offtopic"], by_label["contradiction"]

    print("\n=== Balayage de seuil (supported vs offtopic) ===")
    print(f"{'seuil':>6}{'faux pos.':>12}{'offtopic vu':>13}{'Youden J':>10}")
    t = 0.20
    while t <= 0.70001:
        fp = sum(1 for s in sup if s < t)
        det = sum(1 for s in off if s < t)
        j = (det / len(off) if off else 0) - (fp / len(sup) if sup else 0)
        print(f"{t:>6.2f}{f'{fp}/{len(sup)}':>12}{f'{det}/{len(off)}':>13}{j:>10.2f}")
        t += 0.05

    print("\n=== Zone sûre (séparation soutenu / hors-sujet) ===")
    sup_min, off_max = min(sup), max(off)
    print(f"plancher des 'supported' (min) = {sup_min:.3f}")
    print(f"plafond  des 'offtopic'  (max) = {off_max:.3f}")
    if off_max < sup_min:
        print(f"→ séparation NETTE : tout seuil dans ]{off_max:.3f} ; {sup_min:.3f}[ "
              f"sépare parfaitement. Reco ≈ {(off_max + sup_min) / 2:.2f}")
    else:
        print(f"→ CHEVAUCHEMENT [{off_max:.3f} … {sup_min:.3f}] : pas de séparation parfaite ; "
              f"privilégier 0 faux positif (seuil ≤ {sup_min:.3f}).")

    reco = round((off_max + sup_min) / 2, 2) if off_max < sup_min else round(sup_min, 2)
    slipped = sum(1 for s in con if s >= reco)
    print("\n=== Angle mort déterministe ===")
    print(f"À seuil {reco:.2f} : {slipped}/{len(con)} contradictions passent inaperçues "
          f"(score ≥ seuil) → c'est ce que le cran LLM (sous-lot C) doit rattraper.")

    print("\n=== Note CJK (à corriger) ===")
    print("MIN_SENTENCE_CHARS=20 dans answer_validator : une phrase chinoise dense "
          "(<20 caractères) serait écartée AVANT scoring — seuil de longueur à adapter au CJK.")


if __name__ == "__main__":
    main()
