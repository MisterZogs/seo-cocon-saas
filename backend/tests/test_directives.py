"""Mode Brief bidirectionnel — consignes de l'agence, par article et globales.

Jusqu'ici le Mode Brief ne va que dans un sens : l'agence remplit le formulaire,
l'outil rend six briefs. Ce test couvre le sens inverse — l'agence écrit des
consignes et elles atteignent réellement le rédacteur.

Le seul contrôle qui compte vraiment est le dernier : **une consigne absente du
prompt n'existe pas**. Tout le reste (transport, validation) n'a de valeur que
s'il aboutit là.

Il vérifie aussi l'inverse : que le prompt cadre explicitement ces consignes
comme éditoriales. Le champ est du texte libre, une agence écrira tôt ou tard
« mets un lien vers la page tarifs » — et le maillage doit rester imposé en code.

Aucun appel réseau.

Usage :
    cd backend && .venv/bin/python -m tests.test_directives
"""

from __future__ import annotations

import asyncio
import sys

from models import (
    ClientForm,
    GenerationMode,
    InterCoconPolicy,
    SearchIntent,
    SerpAnalysis,
    ValidatedCocon,
    ValidationDecision,
)
from pipeline.article_generator import (
    _build_brief_prompt,
    _build_cached_context,
    _build_full_prompt,
)
from pipeline.cocon_builder import CoconBuilder
from pipeline.validation import apply_decision
from tests.test_resume import FakeAnthropic

FORM = ClientForm(
    product="Désinsectisation",
    description="Destruction de nids de frelons et de guêpes en Île-de-France.",
    seed_keywords=["nid de frelons"],
    audience="Particuliers en zone pavillonnaire",
    niche="Services à domicile",
    num_cocoons=1,
    mode=GenerationMode.FULL,
)

PROPOSALS = [
    {
        "theme": "Nids de frelons",
        "main_keyword": "nid de frelons",
        "rationale": "Cluster le plus recherché.",
        "mother": {
            "target_keyword": "nid de frelons",
            "h1_title": "Nid de frelons : que faire",
            "meta_title": "Nid de frelons",
            "meta_description": "Guide complet.",
            "slug": "nid-de-frelons",
            "intent": "informational",
        },
        "daughters": [
            {
                "target_keyword": f"frelon sujet {i}",
                "h1_title": f"Frelon sujet {i}",
                "meta_title": f"Frelon {i}",
                "meta_description": "Description.",
                "slug": f"frelon-sujet-{i}",
                "intent": "informational",
            }
            for i in range(1, 5)
        ],
    }
]

MOTHER_DIRECTIVE = "Insister sur le danger du frelon asiatique. Ne jamais conseiller de traiter soi-mêmeses."
DAUGHTER_DIRECTIVE = "Ne pas parler de prix, le client refuse d'afficher des tarifs."

ANALYSIS = SerpAnalysis(
    keyword="nid de frelons",
    scraped_pages_count=8,
    serp_urls_count=10,
    key_entities=["frelon asiatique"],
    key_topics=["destruction de nid"],
    common_questions=["Combien coûte une intervention ?"],
    content_gaps=["retour d'expérience terrain"],
    avg_word_count=1500,
    recommended_word_count=1600,
    recommended_h2_count=6,
    avg_h2_count=6,
    avg_h3_count=5,
    competitive_angle="Angle terrain",
    top_result_format="guide long",
    paa_questions=[],
    serp_features=[],
)


def _check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond else ""))
    return cond


def _decision(directives: dict[str, str]) -> ValidationDecision:
    return ValidationDecision(
        cocoons=[
            ValidatedCocon(
                index=0,
                mother_keyword="nid de frelons",
                daughter_keywords=[f"frelon sujet {i}" for i in range(1, 5)],
                directives=directives,
            )
        ]
    )


# ============================================================


def test_orphan_directive() -> bool:
    print("\n[1] Une consigne orpheline est refusée")
    ok = True

    # Sans ce garde-fou, l'agence croirait avoir donné une instruction qui
    # n'atteint jamais le rédacteur — le pire des deux mondes.
    try:
        _decision({"mot-cle qui n existe pas": "Consigne perdue"})
        ok &= _check("clé inconnue → erreur de validation", False, "acceptée en silence")
    except Exception as e:
        ok &= _check("clé inconnue → erreur de validation", True)
        ok &= _check("… le message nomme la clé fautive", "mot-cle qui n existe pas" in str(e), str(e)[:120])

    try:
        _decision({"NID DE FRELONS": "Casse différente"})
        ok &= _check("clé en casse différente acceptée", True)
    except Exception as e:
        ok &= _check("clé en casse différente acceptée", False, str(e)[:120])
    return ok


def test_transport() -> bool:
    print("\n[2] Transport : écran de validation → ArticleStub")
    ok = True

    decision = _decision(
        {
            "nid de frelons": MOTHER_DIRECTIVE,
            "  Frelon Sujet 2  ": DAUGHTER_DIRECTIVE,  # casse et espaces parasites
            "frelon sujet 3": "   ",  # vide : ne doit rien poser
        }
    )
    rebuilt = asyncio.run(apply_decision(decision, PROPOSALS, [], FORM, FakeAnthropic()))
    cocoons = CoconBuilder().build(rebuilt)
    ok &= _check("1 cocon reconstruit", len(cocoons) == 1, f"{len(cocoons)}")
    if not cocoons:
        return False

    cocon = cocoons[0]
    by_kw = {d.target_keyword: d for d in cocon.daughters}

    ok &= _check(
        "consigne de la mère portée par le stub",
        cocon.mother.directives == MOTHER_DIRECTIVE,
        repr(cocon.mother.directives),
    )
    ok &= _check(
        "consigne d'une fille retrouvée malgré casse et espaces",
        by_kw["frelon sujet 2"].directives == DAUGHTER_DIRECTIVE,
        repr(by_kw["frelon sujet 2"].directives),
    )
    ok &= _check(
        "une consigne vide ne pose rien",
        by_kw["frelon sujet 3"].directives is None,
        repr(by_kw["frelon sujet 3"].directives),
    )
    ok &= _check(
        "un article sans consigne reste à None",
        by_kw["frelon sujet 1"].directives is None,
        repr(by_kw["frelon sujet 1"].directives),
    )

    # Les stubs traversent les checkpoints : c'est ce qui fait survivre les
    # consignes à une reprise, sans code dédié. On le vérifie plutôt que de le
    # supposer.
    revived = type(cocon).model_validate(cocon.model_dump(mode="json"))
    ok &= _check(
        "les consignes survivent à un aller-retour JSON (checkpoint)",
        revived.mother.directives == MOTHER_DIRECTIVE,
        repr(revived.mother.directives),
    )
    return ok


def test_prompts() -> bool:
    print("\n[3] Les consignes arrivent DANS le prompt")
    ok = True

    decision = _decision({"nid de frelons": MOTHER_DIRECTIVE})
    rebuilt = asyncio.run(apply_decision(decision, PROPOSALS, [], FORM, FakeAnthropic()))
    cocon = CoconBuilder().build(rebuilt)[0]

    for label, build in (
        ("Brief", _build_brief_prompt),
        ("Full", _build_full_prompt),
    ):
        prompt = build(cocon.mother, cocon, ANALYSIS, InterCoconPolicy.STRICT, [])
        ok &= _check(
            f"prompt {label} : la consigne y est mot pour mot",
            MOTHER_DIRECTIVE in prompt,
            "absente du prompt — la fonctionnalité n'existe pas",
        )
        ok &= _check(
            f"prompt {label} : cadrée comme éditoriale, pas structurelle",
            "do NOT override the internal linking plan" in prompt.replace("\n", " ")
            or "NOT override the internal linking" in prompt.replace("\n", " "),
        )
        # Le bloc doit venir après l'analyse SERP : une instruction noyée en
        # tête de prompt derrière 3 000 mots d'analyse passe mal.
        ok &= _check(
            f"prompt {label} : consignes placées après l'analyse SERP",
            prompt.index(MOTHER_DIRECTIVE) > prompt.index("Competitive angle to take"),
        )

        sister = cocon.daughters[0]
        clean = build(sister, cocon, ANALYSIS, InterCoconPolicy.STRICT, [])
        ok &= _check(
            f"prompt {label} : un article sans consigne n'a pas la section",
            "AGENCY INSTRUCTIONS FOR THIS SPECIFIC ARTICLE" not in clean,
        )
        ok &= _check(
            f"prompt {label} : et surtout pas la consigne d'un autre article",
            MOTHER_DIRECTIVE not in clean,
            "fuite d'une consigne d'un article vers un autre",
        )
    return ok


def test_global_guidelines() -> bool:
    print("\n[4] Consignes globales du formulaire")
    ok = True

    plain = _build_cached_context(FORM, [])
    ok &= _check(
        "sans consignes : aucune section parasite",
        "AGENCY EDITORIAL GUIDELINES" not in plain,
    )

    guidelines = "Ton direct, tutoiement proscrit. Aucune promesse de résultat."
    with_rules = FORM.model_copy(update={"editorial_guidelines": guidelines})
    context = _build_cached_context(with_rules, [])
    ok &= _check("consignes globales présentes dans le contexte", guidelines in context)
    ok &= _check(
        "… dans le contexte PARTAGÉ, donc facturées une fois",
        "apply to EVERY article" in context,
    )
    ok &= _check(
        "… et elles ne priment pas sur les règles structurelles",
        "enforced in" in context and "code" in context,
    )
    return ok


def main_() -> int:
    print("=" * 60)
    print("CONSIGNES DE L'AGENCE — Mode Brief bidirectionnel")
    print("=" * 60)
    ok = test_orphan_directive()
    ok &= test_transport()
    ok &= test_prompts()
    ok &= test_global_guidelines()
    print("\n" + "=" * 60)
    print("TOUS LES CONTRÔLES PASSENT" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_())
