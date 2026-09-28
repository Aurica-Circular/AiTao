# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Benchmark — answer_validator phase 1 latency (US-076).

Measures the real cost of the deterministic grounding check on THIS machine, so
the fiabilité/rapidité trade-off can be decided from data, not guesses. Loads the
production embedding model (bge-m3) and times ``evaluate_grounding()`` on
realistic answer/context sizes, breaking the cost into sentence-encoding vs
context-encoding — the latter is reusable from LanceDB (already embedded) and is
therefore the compressible part if we optimise later.

Run: python scripts/benchmark_answer_validator.py
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aitao.llm.answer_validator import evaluate_grounding, split_sentences  # noqa: E402


class _Doc:
    """Minimal stand-in for a ContextDocument (only .content is read)."""

    def __init__(self, content: str):
        self.content = content


# Realistic French answer sentences (notary / lease-contract domain).
_SENTENCES = [
    "Le préavis de résiliation du bail est fixé à un mois en zone tendue.",
    "Le loyer mensuel s'élève à 1 200 euros, charges comprises.",
    "Le dépôt de garantie correspond à un mois de loyer hors charges.",
    "Le contrat prend effet le 1er septembre 2024 pour une durée de trois ans.",
    "Toute révision annuelle est indexée sur l'IRL publié par l'INSEE.",
    "Le locataire doit souscrire une assurance habitation dès l'entrée dans les lieux.",
    "Les réparations locatives courantes restent à la charge du locataire.",
    "Le congé doit être notifié par lettre recommandée avec accusé de réception.",
    "En cas de retard de paiement, une clause résolutoire peut être activée.",
    "Le bailleur s'engage à délivrer un logement décent et en bon état d'usage.",
    "La colocation est autorisée sous réserve de l'accord écrit du propriétaire.",
    "Un état des lieux contradictoire est établi à l'entrée et à la sortie.",
]

_CHUNKS = [
    "Article 3 - Duree et preavis. Le present bail est conclu pour trois ans. "
    "En zone tendue, le preavis de depart du locataire est reduit a un mois. "
    "Le conge est notifie par lettre recommandee avec accuse de reception.",
    "Article 4 - Loyer et charges. Le loyer mensuel est fixe a 1 200 euros "
    "charges comprises. La revision annuelle suit l'indice IRL de l'INSEE.",
    "Article 5 - Depot de garantie. Un depot equivalent a un mois de loyer hors "
    "charges est verse a la signature et restitue apres l'etat des lieux de sortie.",
    "Article 6 - Obligations du locataire. Souscrire une assurance habitation, "
    "entretenir le logement et assumer les reparations locatives courantes.",
    "Article 7 - Obligations du bailleur. Delivrer un logement decent, en bon "
    "etat d'usage, et garantir la jouissance paisible des lieux.",
]


def _load_model():
    """Load the production embedding model (cached); report load time."""
    from sentence_transformers import SentenceTransformer

    name = "BAAI/bge-m3"
    print(f"Loading embedding model {name} ...", flush=True)
    t0 = time.perf_counter()
    model = SentenceTransformer(name)
    print(f"  loaded in {time.perf_counter() - t0:.1f}s\n", flush=True)
    return model


def _time_encode(model, texts, runs):
    """Median wall-clock ms to encode ``texts`` (after one warm-up call)."""
    model.encode(list(texts), convert_to_numpy=True)  # warm
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        model.encode(list(texts), convert_to_numpy=True)
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def _scenario(label, n_sent, n_ctx, model, runs=5):
    """Time the full grounding check + the encode breakdown for one size."""
    answer = " ".join(_SENTENCES[:n_sent])
    ctx = [_Doc(c) for c in _CHUNKS[:n_ctx]]

    def embed(texts):
        return model.encode(list(texts), convert_to_numpy=True)

    evaluate_grounding(answer, ctx, embed)  # warm-up

    totals, report = [], None
    for _ in range(runs):
        report = evaluate_grounding(answer, ctx, embed)
        totals.append(report.elapsed_ms)

    sents = split_sentences(answer)
    return {
        "label": label,
        "sent": len(sents),
        "ctx": n_ctx,
        "total": statistics.median(totals),
        "lo": min(totals),
        "hi": max(totals),
        "enc_sent": _time_encode(model, sents, runs),
        "enc_ctx": _time_encode(model, _CHUNKS[:n_ctx], runs),
        "score": report.grounding_score if report else 0.0,
    }


def main():
    model = _load_model()
    scenarios = [
        _scenario("Réponse courte", 3, 3, model),
        _scenario("Réponse moyenne", 6, 5, model),
        _scenario("Réponse longue", 12, 5, model),
    ]

    header = (
        f"{'Scénario':<18}{'phr.':>5}{'chk.':>5}"
        f"{'total (méd)':>13}{'min':>8}{'max':>8}"
        f"{'enc.phr.':>10}{'enc.chk.*':>11}{'ancr.':>7}"
    )
    print(header)
    print("-" * len(header))
    for s in scenarios:
        print(
            f"{s['label']:<18}{s['sent']:>5}{s['ctx']:>5}"
            f"{s['total']:>10.0f} ms{s['lo']:>6.0f}ms{s['hi']:>6.0f}ms"
            f"{s['enc_sent']:>7.0f}ms{s['enc_ctx']:>9.0f}ms{s['score']:>7.2f}"
        )
    print(
        "\n* enc.chk. = encodage des chunks de contexte — compressible : "
        "réutilisable depuis LanceDB (déjà embarqué à l'indexation)."
    )
    print(
        "  Coût incompressible ≈ enc.phr. (les phrases de la réponse, "
        "inconnues avant génération)."
    )


if __name__ == "__main__":
    main()
