"""Règles de facturation au cocon — partie pure, sans base de données.

Le modèle est **à l'usage, pas à l'abonnement illimité** : chaque unité produite
coûte de l'argent réel, un forfait illimité serait une prime au client le plus
lourd. Les paliers et les règles viennent de CLAUDE.md, section « Pricing ».

Les cinq règles qui gouvernent tout le reste :

1. **Débit à la génération, jamais à la soumission.** La recherche de mots-clés
   est offerte (~$0,38) : c'est l'essai gratuit et le moment de validation.
2. **Un run échoué est remboursé**, automatiquement.
3. **Une reprise sur checkpoint ne re-débite jamais** — elle répare un échec
   technique dont le client n'est pas responsable.
4. **Une régénération d'article se débite**, elle : c'est un travail que
   l'agence commande. Granularité : l'article, soit 1/6 de cocon.
5. **Report des cocons non consommés sur un mois**, plafonné à 1× l'allocation.

La règle 4 est la raison d'être de `UNITS_PER_COCOON` : le solde doit savoir
descendre au sixième, et 1/6 n'a pas d'écriture décimale exacte. Tout est donc
compté en entiers d'unités et converti à l'affichage seulement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# 1 cocon = 1 mère + 5 filles au cas nominal, et la régénération se débite à
# l'article. Le dénominateur est celui de la décision commerciale, pas le nombre
# réel d'articles du cocon (qui varie de 4 à 6) : une régénération coûte 1/6 de
# cocon quelle que soit la taille du cocon, parce que c'est ce qui est annoncé.
UNITS_PER_COCOON = 6


@dataclass(frozen=True)
class Plan:
    key: str
    label: str
    monthly_price_eur: int
    cocoons_per_month: int

    @property
    def units_per_month(self) -> int:
        return self.cocoons_per_month * UNITS_PER_COCOON


# Grille arrêtée en août 2026. L'entrée à 49 € est un choix assumé : c'est le
# prix d'entrée payant le plus bas du marché FR (Cocon.se 20 €, Hack the SEO
# Growth+ 69 €, SEOQuantum 89 €, YourTextGuru 90 €, Sedestral 119 €).
PLANS: dict[str, Plan] = {
    # L'essai n'a pas d'allocation récurrente : il reçoit un lot unique à
    # l'inscription (cf. TRIAL_COCOONS), et c'est tout.
    "trial": Plan("trial", "Essai", 0, 0),
    "independant": Plan("independant", "Indépendant", 49, 3),
    "agence": Plan("agence", "Agence", 249, 20),
    "studio": Plan("studio", "Studio", 690, 60),
}

DEFAULT_PLAN = "trial"

# 3 cocons sans carte bancaire. Coût maximal pour nous : ~12 €.
TRIAL_COCOONS = 3
TRIAL_VALIDITY_DAYS = 30


def get_plan(key: str | None) -> Plan:
    """Formule d'une agence. Une clé inconnue retombe sur l'essai.

    Retomber sur l'essai plutôt que lever : une valeur de plan corrompue en base
    ne doit pas empêcher l'agence de se connecter, seulement de générer.
    """
    return PLANS.get(key or "", PLANS[DEFAULT_PLAN])


def period_key(moment: datetime | None = None) -> str:
    """Identifiant de la période d'allocation courante, au format 'YYYY-MM'.

    Le rattachement est au **mois calendaire**, pas à la date anniversaire de
    l'abonnement. C'est moins juste au prorata, mais il n'y a pas encore de
    prestataire de paiement pour faire foi sur les dates de facturation :
    inventer un cycle glissant ici créerait deux vérités le jour où Stripe
    arrive. À revoir avec la facturation réelle.
    """
    now = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def period_bounds(key: str) -> tuple[datetime, datetime]:
    """(début de la période, expiration du lot) pour une clé 'YYYY-MM'.

    Le lot vit **deux périodes**. C'est ce qui implémente le report d'un mois :
    au plus deux lots d'abonnement sont vivants en même temps, donc le report
    est plafonné à 1× l'allocation sans qu'aucun calcul de report n'existe.
    """
    year, month = (int(part) for part in key.split("-"))
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end_month = month + 2
    end_year = year + (end_month - 1) // 12
    end_month = (end_month - 1) % 12 + 1
    return start, datetime(end_year, end_month, 1, tzinfo=timezone.utc)


def cocoons_to_units(cocoons: int) -> int:
    return cocoons * UNITS_PER_COCOON


def units_to_cocoons(units: int) -> float:
    """Pour l'affichage uniquement — ne jamais réinjecter le résultat en calcul."""
    return units / UNITS_PER_COCOON


def format_cocoons(units: int) -> str:
    """« 2 cocons », « 2,5 cocons », « 1/6 de cocon » — pour les messages FR."""
    if units == 0:
        return "0 cocon"
    whole, rest = divmod(units, UNITS_PER_COCOON)
    if rest == 0:
        return f"{whole} cocon{'s' if whole > 1 else ''}"
    fraction = f"{rest}/{UNITS_PER_COCOON}"
    if whole == 0:
        return f"{fraction} de cocon"
    return f"{whole} + {fraction} cocon{'s' if whole > 1 else ''}"


class InsufficientBalance(Exception):
    """Solde insuffisant pour l'opération demandée.

    Portée jusqu'à l'API, où elle devient un 402 : c'est le seul statut HTTP qui
    dise « la requête est valide, il manque le paiement ».
    """

    def __init__(self, *, required_units: int, available_units: int) -> None:
        self.required_units = required_units
        self.available_units = available_units
        super().__init__(
            f"Solde insuffisant : {format_cocoons(required_units)} nécessaire(s), "
            f"{format_cocoons(available_units)} disponible(s)."
        )
