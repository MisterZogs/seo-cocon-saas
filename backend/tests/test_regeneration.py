"""Régénération d'un article — chantier 16b.

Deux choses seulement méritent d'être prouvées ici, et elles sont toutes les
deux des façons de perdre de l'argent ou la promesse produit :

**1. Le maillage reste exact après réécriture.** C'est LE différenciateur
vendable — « chaque page reçoit exactement 5 liens entrants, comptez ». Une
régénération qui laisserait une page orpheline le détruirait plus sûrement
qu'un bug visible, parce que personne ne le verrait. Le test fait donc rendre au
modèle un plan de liens *délibérément cassé* (un lien vers un slug inexistant,
un lien vers soi-même, trois liens manquants) et vérifie que la normalisation
reconstruit les 30 liens.

**2. Un échec de régénération ne rembourse QUE la régénération.** `refund_run`
ne cible que les `debit_generation` : l'appeler sur une régénération ratée
rembourserait le cocon d'origine du run, soit un cocon offert à chaque échec.
Le test le vérifie contre un vrai Postgres, parce que c'est du SQL.

Aucun appel réseau. La partie SQL est ignorée proprement sans `pgserver`.

Usage :
    cd backend && .venv/bin/python -m tests.test_regeneration
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from clients.anthropic_client import MODELS, CompletionResult, ModelTier, UsageTotals
from models import (
    ArticleBrief,
    ArticleStub,
    ArticleType,
    ClientForm,
    ExperienceElement,
    GenerationMode,
    InterCoconPolicy,
    PipelineResult,
    RunUsage,
    SearchIntent,
    SerpAnalysis,
)
from pipeline.cocon_builder import CoconBuilder
from pipeline.maillage import assemble_maillage, audit_maillage
from pipeline.regeneration import ArticleNotFound, regenerate_article


def _check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond else ""))
    return bool(cond)


# ============================================================
# Fixtures — un run terminé, en Mode Brief, 1 mère + 5 filles
# ============================================================

FORM = ClientForm(
    product="Désinsectisation",
    description="Destruction de nids de frelons et de guêpes en Île-de-France.",
    seed_keywords=["nid de frelons"],
    audience="Particuliers en zone pavillonnaire",
    niche="Services à domicile",
    num_cocoons=1,
    mode=GenerationMode.BRIEF,
)

PROPOSAL = {
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
        for i in range(1, 6)
    ],
}

CIBLE = "frelon-sujet-2"

ANALYSIS = SerpAnalysis(
    keyword="nid de frelons",
    scraped_pages_count=8,
    serp_urls_count=10,
    key_entities=["frelon asiatique"],
    key_topics=["destruction de nid"],
    common_questions=["Combien coûte une intervention ?"],
    content_gaps=["Le coût réel"],
    avg_word_count=1400,
    competitive_angle="Angle terrain",
    recommended_word_count=1600,
)


class RegenAnthropic:
    """Doublure qui rend un plan de liens DÉLIBÉRÉMENT cassé.

    C'est le point : si la doublure rendait un maillage correct, le test
    prouverait que le modèle sait mailler — ce qu'on sait faux, c'est
    justement la raison d'être de `pipeline/maillage.py`.
    """

    def __init__(self, fail: bool = False) -> None:
        self.usage = UsageTotals()
        self.prompts: list[str] = []
        self.contexts: list[str] = []
        self.fail = fail

    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        user_prompt: str,
        max_tokens: int = 4096,
        cached_context: str | None = None,
    ) -> tuple[dict, Any]:
        self.prompts.append(user_prompt)
        self.contexts.append(cached_context or "")
        if self.fail:
            raise RuntimeError("Error code: 400 - Your credit balance is too low")

        result = CompletionResult(
            text="{}",
            model=MODELS[model],
            input_tokens=800,
            output_tokens=1000,
            cache_read_tokens=0,
            stop_reason="end_turn",
        )
        self.usage.add(model, result)

        bad_links = [
            # cible inexistante → doit être nettoyée
            {"anchor_text": "page fantôme", "target_slug": "page-qui-nexiste-pas"},
            # lien vers soi-même → doit être retiré
            {"anchor_text": "moi-même", "target_slug": CIBLE},
            # un seul lien légitime sur les cinq attendus
            {"anchor_text": "nid de frelons", "target_slug": "nid-de-frelons"},
        ]
        common = {
            "sections": [{"h2": "Section", "h3s": [], "key_points": ["a"]}],
            "external_links_suggestions": [],
            "external_links": [],
        }
        if "BRIEF" in system.upper() or "brief" in system.lower():
            return {
                **common,
                "faq_questions": ["Q ?"],
                "internal_links_plan": bad_links,
                "editorial_notes": "Notes régénérées.",
                "tone_guidance": "Pro.",
                "unique_angle": "Nouvel angle.",
            }, result
        return {
            **common,
            "faq": [{"question": "Q ?", "answer": "R."}],
            "internal_links": bad_links,
            "content_markdown": "# Article régénéré\n\nContenu neuf.",
            "schema_jsonld": {"@type": "Article"},
            "word_count": 1200,
            "eeat_score": {
                "experience": 80,
                "expertise": 70,
                "authoritativeness": 65,
                "trustworthiness": 70,
                "overall": 71,
                "warnings": [],
            },
        }, result


def _finished_run(
    mode: GenerationMode = GenerationMode.BRIEF,
    directives: dict[str, str] | None = None,
    experience: list[ExperienceElement] | None = None,
) -> PipelineResult:
    """Un run tel qu'il ressort du pipeline : maillage déjà normalisé."""
    form = FORM.model_copy(update={"mode": mode, "experience_elements": experience or []})
    cocoons = CoconBuilder().build([PROPOSAL])
    assert cocoons, "fixture invalide"
    cocon = cocoons[0]

    for stub in [cocon.mother, *cocon.daughters]:
        stub.directives = (directives or {}).get(stub.slug)

    briefs: list[ArticleBrief] = []
    articles: list[Any] = []
    if mode is GenerationMode.BRIEF:
        briefs = [
            ArticleBrief(
                stub=stub,
                serp_analysis=ANALYSIS,
                sections=[],
                faq_questions=[],
                internal_links_plan=[],
                external_links_suggestions=[],
                editorial_notes="Notes d'origine.",
                tone_guidance="Ton d'origine.",
                unique_angle="Angle d'origine.",
            )
            for stub in [cocon.mother, *cocon.daughters]
        ]
    else:
        from models import GeneratedArticle

        articles = [
            GeneratedArticle(
                stub=stub,
                serp_analysis=ANALYSIS,
                sections=[],
                faq=[],
                internal_links=[],
                external_links=[],
                schema_jsonld={},
                content_markdown="# Origine\n\nTexte.",
                experience_used=(["exp-1"] if stub.slug == "frelon-sujet-1" else []),
                word_count=100,
            )
            for stub in [cocon.mother, *cocon.daughters]
        ]

    maillage = assemble_maillage(
        briefs=briefs, articles=articles, cocoons=cocoons, policy=form.inter_cocon_policy
    )
    return PipelineResult(
        form=form,
        keywords_researched=[],
        cocoons=cocoons,
        briefs=briefs,
        articles=articles,
        maillage_map=maillage,
        backlink_reports=[],
        usage=RunUsage(claude_calls=6, claude_cost_usd=0.5, dataforseo_cost_usd=0.38),
    )


def _maillage_stats(result: PipelineResult) -> dict[str, Any]:
    links = result.maillage_map.links
    inbound: dict[str, int] = {s: 0 for s in links}
    total = 0
    for lst in links.values():
        for l in lst:
            inbound[l.target_slug] = inbound.get(l.target_slug, 0) + 1
            total += 1
    non_reciproques = [
        f"{a}→{l.target_slug}"
        for a, lst in links.items()
        for l in lst
        if not any(x.target_slug == a for x in links.get(l.target_slug, []))
    ]
    return {
        "pages": len(links),
        "total": total,
        "outbound": {s: len(lst) for s, lst in links.items()},
        "inbound": inbound,
        "non_reciproques": non_reciproques,
    }


# ============================================================
# 1. Le maillage reste exact
# ============================================================


async def test_maillage() -> bool:
    print("\n[1] Le maillage survit à la régénération")
    ok = True

    result = _finished_run()
    avant = _maillage_stats(result)
    ok &= _check("état initial : 30 liens", avant["total"] == 30, str(avant["total"]))

    fake = RegenAnthropic()
    outcome = await regenerate_article(
        result, slug=CIBLE, directives="Insister sur le danger.", anthropic=fake
    )
    apres = _maillage_stats(outcome.result)

    ok &= _check("6 pages maillées", apres["pages"] == 6, str(apres["pages"]))
    ok &= _check("30 liens au total", apres["total"] == 30, str(apres["total"]))
    ok &= _check(
        "5 liens sortants sur chaque page",
        all(n == 5 for n in apres["outbound"].values()),
        str(apres["outbound"]),
    )
    ok &= _check(
        "5 liens entrants sur chaque page",
        all(n == 5 for n in apres["inbound"].values()),
        str(apres["inbound"]),
    )
    ok &= _check(
        "aucun lien non réciproque",
        not apres["non_reciproques"],
        str(apres["non_reciproques"]),
    )

    audit = audit_maillage(outcome.result.maillage_map, outcome.result.cocoons)
    ok &= _check("aucune page orpheline", not audit["orphans"], str(audit["orphans"]))
    ok &= _check(
        "aucun lien obligatoire manquant",
        not audit["missing_required"],
        str(audit["missing_required"]),
    )
    ok &= _check("aucun lien inter-cocon", audit["cross_cocon_count"] == 0)
    ok &= _check("l'outcome se déclare intact", outcome.maillage_intact)

    # Le point précis : le modèle avait rendu 3 liens dont 2 invalides.
    regenere = outcome.result.maillage_map.links[CIBLE]
    cibles = {l.target_slug for l in regenere}
    ok &= _check("le lien vers un slug inexistant est retiré", "page-qui-nexiste-pas" not in cibles)
    ok &= _check("le lien vers soi-même est retiré", CIBLE not in cibles)
    ok &= _check(
        "les 4 liens manquants sont reconstruits",
        cibles == {"nid-de-frelons", "frelon-sujet-1", "frelon-sujet-3", "frelon-sujet-4", "frelon-sujet-5"},
        str(sorted(cibles)),
    )
    return ok


# ============================================================
# 2. Les consignes
# ============================================================


async def test_consignes() -> bool:
    print("\n[2] Les consignes atteignent le prompt et remplacent les anciennes")
    ok = True

    ancienne = "Ne pas parler de prix, le client refuse d'afficher des tarifs."
    nouvelle = "Finalement, donner une fourchette de prix indicative."

    result = _finished_run(directives={CIBLE: ancienne})
    fake = RegenAnthropic()
    outcome = await regenerate_article(
        result, slug=CIBLE, directives=nouvelle, anthropic=fake
    )

    prompt = fake.prompts[0]
    ok &= _check("un seul appel au modèle", len(fake.prompts) == 1, str(len(fake.prompts)))
    ok &= _check("la nouvelle consigne est dans le prompt, mot pour mot", nouvelle in prompt)
    ok &= _check("l'ancienne consigne a disparu du prompt", ancienne not in prompt)
    ok &= _check("l'outcome rapporte l'ancienne consigne", outcome.previous_directives == ancienne)

    # Sans ça, une deuxième régénération repartirait de la consigne d'origine :
    # ce sont deux objets distincts après désérialisation.
    stub_cocon = next(
        s
        for c in outcome.result.cocoons
        for s in [c.mother, *c.daughters]
        if s.slug == CIBLE
    )
    ok &= _check("la consigne est persistée sur le stub du cocon", stub_cocon.directives == nouvelle)
    brief = next(b for b in outcome.result.briefs if b.stub.slug == CIBLE)
    ok &= _check("… et sur le stub du livrable", brief.stub.directives == nouvelle)
    ok &= _check("le brief est bien le neuf", brief.unique_angle == "Nouvel angle.")
    ok &= _check(
        "les autres articles ne sont pas touchés",
        all(
            b.unique_angle == "Angle d'origine."
            for b in outcome.result.briefs
            if b.stub.slug != CIBLE
        ),
    )

    # Une régénération sans consigne efface l'ancienne plutôt que de la garder.
    result2 = _finished_run(directives={CIBLE: ancienne})
    out2 = await regenerate_article(
        result2, slug=CIBLE, directives=None, anthropic=RegenAnthropic()
    )
    stub2 = next(b for b in out2.result.briefs if b.stub.slug == CIBLE).stub
    ok &= _check("régénérer sans consigne efface l'ancienne", stub2.directives is None)
    return ok


# ============================================================
# 3. Article inconnu, et unicité du verbatim en mode FULL
# ============================================================


async def test_garde_fous() -> bool:
    print("\n[3] Garde-fous")
    ok = True

    result = _finished_run()
    try:
        await regenerate_article(
            result, slug="slug-inconnu", directives=None, anthropic=RegenAnthropic()
        )
        ok &= _check("un slug inconnu lève ArticleNotFound", False, "aucune exception")
    except ArticleNotFound as e:
        ok &= _check("un slug inconnu lève ArticleNotFound", True)
        ok &= _check("… et le message liste les articles disponibles", CIBLE in str(e))

    # Mode FULL : l'élément d'expérience déjà placé dans une AUTRE fille reste
    # verrouillé, sinon le même bloc verbatim se retrouve deux fois dans le silo.
    exp = [
        ExperienceElement(
            id="exp-1",
            kind="case_study",
            title="Intervention à Meudon",
            content="Nous sommes intervenus sur un nid de 40 cm en août 2025." * 3,
        )
    ]
    full = _finished_run(mode=GenerationMode.FULL, experience=exp)
    porteur = next(a for a in full.articles if a.stub.slug == "frelon-sujet-1")
    ok &= _check("fixture : l'élément est porté par une autre fille", porteur.experience_used == ["exp-1"])

    out = await regenerate_article(
        full, slug=CIBLE, directives=None, anthropic=RegenAnthropic()
    )
    regenere = next(a for a in out.result.articles if a.stub.slug == CIBLE)
    ok &= _check(
        "l'élément déjà placé ailleurs n'est pas réinjecté",
        "exp-1" not in regenere.experience_used,
        str(regenere.experience_used),
    )
    ok &= _check(
        "l'article qui le portait le porte toujours",
        next(a for a in out.result.articles if a.stub.slug == "frelon-sujet-1").experience_used
        == ["exp-1"],
    )
    ok &= _check("le maillage FULL reste à 30 liens", _maillage_stats(out.result)["total"] == 30)
    return ok


# ============================================================
# 4. Le coût de la régénération s'ajoute à celui du run
# ============================================================


async def test_usage() -> bool:
    print("\n[4] Comptabilité du coût")
    ok = True

    result = _finished_run()
    avant = result.usage
    out = await regenerate_article(
        result, slug=CIBLE, directives=None, anthropic=RegenAnthropic()
    )
    apres = out.result.usage

    ok &= _check("le nombre d'appels augmente", apres.claude_calls == avant.claude_calls + 1)
    ok &= _check("le coût Claude augmente", apres.claude_cost_usd > avant.claude_cost_usd)
    ok &= _check(
        "le coût DataForSEO ne bouge pas (l'analyse SERP est relue)",
        apres.dataforseo_cost_usd == avant.dataforseo_cost_usd,
    )
    return ok


# ============================================================
# 5. Facturation — contre un vrai PostgreSQL
# ============================================================


async def _run_db_tests(dsn: str) -> bool:
    import db.postgres as pg
    from db.billing import BillingRepository

    repo = pg.RunRepository(dsn)
    pg._repository = repo
    billing = BillingRepository()
    pool = await repo.require_pool()

    ok = True
    print("\n[5] Débit et remboursement d'une régénération (PostgreSQL réel)")

    async with pool.acquire() as conn:
        agency_id = str(
            await conn.fetchval(
                "insert into agencies (email, name, password_hash, plan) "
                "values ($1, $1, 'x', 'trial') returning id",
                "regen@test.local",
            )
        )
    run_id = "11111111-1111-1111-1111-111111111111"
    from billing import get_plan

    plan = get_plan("trial")

    async def solde() -> int:
        return await billing.balance_units(agency_id, plan)

    depart = await solde()
    ok &= _check("essai : 18 unités (3 cocons)", depart == 18, str(depart))

    # Le run consomme d'abord un cocon, comme une vraie génération.
    await billing.debit_generation(
        agency_id=agency_id, run_id=run_id, cocoons=1, plan=plan
    )
    ok &= _check("après génération : 12 unités", await solde() == 12, str(await solde()))

    # Une régénération débite 1 unité — 1/6 de cocon, un article.
    ids = await billing.debit_regeneration(
        agency_id=agency_id, run_id=run_id, articles=1, plan=plan
    )
    ok &= _check("après régénération : 11 unités", await solde() == 11, str(await solde()))
    ok &= _check("le débit rend l'identifiant de son écriture", len(ids) == 1, str(ids))

    # 🔴 Le contrôle central : rembourser la régénération ne doit PAS rembourser
    # le cocon d'origine. `refund_run` l'aurait fait, d'où `reverse_entries`.
    rendu = await billing.reverse_entries(ids, note="test — régénération en échec")
    apres = await solde()
    ok &= _check("le remboursement rend 1 unité", rendu == 1, str(rendu))
    ok &= _check(
        "solde revenu à 12, PAS à 18 — le cocon d'origine reste débité",
        apres == 12,
        f"{apres} (18 = le cocon a été offert)",
    )

    async with pool.acquire() as conn:
        gen_reversed = await conn.fetchval(
            "select reversed_at from cocoon_ledger "
            "where run_id = $1::uuid and kind = 'debit_generation'",
            run_id,
        )
    ok &= _check("le débit de génération n'est pas annulé", gen_reversed is None)

    # Rejouer le remboursement ne doit rien rendre de plus.
    encore = await billing.reverse_entries(ids, note="test — rejeu")
    ok &= _check("le remboursement est idempotent", encore == 0 and await solde() == 12)

    # Deux régénérations du même article sont deux commandes distinctes.
    a = await billing.debit_regeneration(
        agency_id=agency_id, run_id=run_id, articles=1, plan=plan
    )
    b = await billing.debit_regeneration(
        agency_id=agency_id, run_id=run_id, articles=1, plan=plan
    )
    ok &= _check(
        "deux régénérations débitent deux fois (non idempotent)",
        await solde() == 10 and a != b,
        str(await solde()),
    )

    # Et le remboursement de l'une ne touche pas l'autre.
    await billing.reverse_entries(a, note="test — ciblage")
    ok &= _check(
        "rembourser une régénération n'annule pas l'autre",
        await solde() == 11,
        str(await solde()),
    )

    await repo.close()
    return ok


# ============================================================


def main_() -> int:
    print("=" * 60)
    print("RÉGÉNÉRATION D'UN ARTICLE (chantier 16b)")
    print("=" * 60)

    ok = asyncio.run(test_maillage())
    ok &= asyncio.run(test_consignes())
    ok &= asyncio.run(test_garde_fous())
    ok &= asyncio.run(test_usage())

    try:
        import pgserver
    except ImportError:
        print("\n[5] Facturation contre PostgreSQL — IGNORÉ")
        print("    `pgserver` n'est pas installé (dépendance de développement).")
        print("    ⚠️ Sans lui, le ciblage du remboursement n'est pas testé —")
        print("    c'est-à-dire la partie qui peut offrir un cocon à chaque échec.")
        print("\n" + "=" * 60)
        print("CONTRÔLES PURS OK — SQL NON EXÉCUTÉ" if ok else "ÉCHEC")
        return 0 if ok else 1

    tmp = Path(tempfile.mkdtemp(prefix="cocon-regen-"))
    server = None
    try:
        server = pgserver.get_server(tmp / "pgdata")
        dsn = server.get_uri()
        os.environ["DATABASE_URL"] = dsn
        ok &= asyncio.run(_run_db_tests(dsn))
    finally:
        try:
            if server is not None:
                server.cleanup()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print("TOUS LES CONTRÔLES PASSENT" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_())
