"""Vérifie `_extract_json` — le parseur qui a fait tomber un run réel.

Contexte : le 2026-08-12, un run FULL en production a échoué à 50 % sur le
6e article (« cocon semantique wordpress »), après avoir payé cinq articles.
Réponse de 41 335 caractères, `stop_reason` normal, JSON refusé par les trois
stratégies d'extraction.

Ce module est le filet de sécurité de la contrainte la plus fragile du produit :
on demande au modèle de placer un article Markdown ENTIER dans une chaîne JSON.
Tout ce qu'un rédacteur écrit naturellement — saut de ligne, bloc de code,
guillemet — est un caractère que JSON veut échappé.

Usage :
    cd backend && .venv/bin/python -m tests.test_json_extraction
"""

from __future__ import annotations

import json
import sys

from clients.anthropic_client import _extract_json

NL = chr(10)
FENCE = "`" * 3


def _check(condition: bool, label: str) -> bool:
    print(f"  {'✓' if condition else '✗'} {label}")
    return condition


def test_json_direct() -> bool:
    print("\n[1] JSON propre")
    ok = _check(_extract_json('{"a": 1}') == {"a": 1}, "objet")
    ok &= _check(_extract_json('[1, 2]') == [1, 2], "tableau")
    ok &= _check(_extract_json('  {"a": 1}  ') == {"a": 1}, "espaces autour")
    return ok


def test_bloc_markdown() -> bool:
    print("\n[2] Entouré d'un bloc de code")
    ok = _check(
        _extract_json(f"{FENCE}json{NL}" + '{"a": 1}' + f"{NL}{FENCE}") == {"a": 1},
        "clôture ```json",
    )
    ok &= _check(
        _extract_json(f"{FENCE}{NL}" + '{"a": 1}' + f"{NL}{FENCE}") == {"a": 1},
        "clôture ``` sans langage",
    )
    ok &= _check(
        _extract_json("Voici :" + NL + f"{FENCE}json{NL}" + '{"a": 1}' + f"{NL}{FENCE}{NL}Voilà.")
        == {"a": 1},
        "bavardage avant et après",
    )
    return ok


def test_bloc_de_code_imbrique() -> bool:
    print("\n[3] 🔴 Bloc de code À L'INTÉRIEUR du markdown généré")
    # Le cas WordPress : l'article montre du PHP, donc content_markdown contient
    # ```php. Une regex non gourmande s'arrêtait à CE ```-là et coupait le JSON.
    article = f"# Titre{NL}{NL}Le code :{NL}{NL}{FENCE}php{NL}add_filter('x');{NL}{FENCE}{NL}{NL}Fin."
    payload = {"content_markdown": article, "word_count": 12}
    texte = f"{FENCE}json{NL}" + json.dumps(payload, ensure_ascii=False) + f"{NL}{FENCE}"

    out = _extract_json(texte)
    ok = _check(out["content_markdown"] == article, "l'article est récupéré ENTIER")
    ok &= _check(FENCE + "php" in out["content_markdown"], "le bloc de code interne survit")
    ok &= _check(out["word_count"] == 12, "les clés qui suivent le markdown ne sont pas perdues")
    return ok


def test_saut_de_ligne_litteral() -> bool:
    print("\n[4] 🔴 Saut de ligne LITTÉRAL dans une chaîne — la panne de production")
    # Ce que produit un modèle qui recopie du markdown sans l'échapper.
    # json.loads refuse par défaut : « Invalid control character ».
    casse = '{"content_markdown": "# Titre' + NL + NL + 'Paragraphe."}'
    ok = _check(
        _extract_json(casse)["content_markdown"] == "# Titre" + NL + NL + "Paragraphe.",
        "récupéré, sauts de ligne compris",
    )

    # Et la même chose dans un bloc de code, comme en production.
    dans_bloc = f"{FENCE}json{NL}" + casse + f"{NL}{FENCE}"
    ok &= _check(
        _extract_json(dans_bloc)["content_markdown"].startswith("# Titre"),
        "idem à l'intérieur d'un bloc ```json",
    )

    # Une tabulation littérale est le même genre de caractère de contrôle.
    tab = '{"x": "a' + chr(9) + 'b"}'
    ok &= _check(_extract_json(tab)["x"] == "a" + chr(9) + "b", "tabulation littérale acceptée")
    return ok


def test_cas_reel_complet() -> bool:
    print("\n[5] Reconstitution du cas réel : gros article + bloc de code + sauts bruts")
    article = (
        "# Cocon sémantique WordPress" + NL * 2
        + "Un cocon organise les pages." + NL * 2
        + "## Le code" + NL * 2
        + f"{FENCE}php{NL}add_filter('the_content', 'x');{NL}{FENCE}" + NL * 2
        + "## FAQ" + NL * 2
        + "### C'est quoi ?" + NL * 2
        + 'Une "arborescence" stricte.' + NL
    )
    # Chaîne JSON construite à la main avec des sauts de ligne LITTÉRAUX : c'est
    # précisément ce que le modèle a produit, et ce que json.loads rejetait.
    brut = '{"content_markdown": "' + article.replace('"', '\\"') + '", "word_count": 30}'
    texte = f"{FENCE}json{NL}" + brut + f"{NL}{FENCE}"

    out = _extract_json(texte)
    ok = _check(out["word_count"] == 30, "la clé après le markdown est lue")
    ok &= _check("## FAQ" in out["content_markdown"], "la FAQ est présente")
    ok &= _check(f"{FENCE}php" in out["content_markdown"], "le bloc PHP est présent")
    ok &= _check('"arborescence"' in out["content_markdown"], "les guillemets échappés survivent")
    return ok


def test_echec_reste_un_echec() -> bool:
    print("\n[6] Ce qui est vraiment irrécupérable échoue — avec un message utile")
    try:
        _extract_json("Je ne peux pas répondre à cette demande.")
        return _check(False, "aurait dû lever")
    except ValueError as e:
        ok = _check(True, "ValueError levée")
        ok &= _check("longueur totale" in str(e), "la longueur est indiquée")

    # Tronqué en plein milieu : irrécupérable, mais le message doit dire OÙ.
    try:
        _extract_json('{"a": "valeur incompl')
        return _check(False, "aurait dû lever")
    except ValueError as e:
        msg = str(e)
        ok &= _check("position" in msg, "la position de l'erreur est donnée")
        ok &= _check("voisinage" in msg, "le voisinage est donné (diagnostic possible)")
    return ok


def main() -> int:
    print("=" * 62)
    print("EXTRACTION JSON — RÉGRESSION DU RUN DU 2026-08-12")
    print("=" * 62)

    results = [
        test_json_direct(),
        test_bloc_markdown(),
        test_bloc_de_code_imbrique(),
        test_saut_de_ligne_litteral(),
        test_cas_reel_complet(),
        test_echec_reste_un_echec(),
    ]

    print("\n" + "=" * 62)
    if all(results):
        print(f"✓ {len(results)}/{len(results)} groupes OK")
        return 0
    print(f"✗ {results.count(False)}/{len(results)} groupe(s) en échec")
    return 1


if __name__ == "__main__":
    sys.exit(main())
