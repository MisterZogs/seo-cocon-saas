"""Paiement Stripe — abonnements et achat de cocons à l'unité.

Trois partis pris, tous destinés à limiter ce qu'il y a à maintenir :

1. **Checkout et Portail hébergés par Stripe.** Aucune donnée de carte ne
   transite par nous, et l'authentification forte, la TVA européenne et les
   relances d'impayé sont gérées chez eux. Écrire ça nous-mêmes serait des
   semaines de travail pour un résultat moins bon.

2. **Aucun produit à créer dans le tableau de bord Stripe.** Les tarifs sont
   déclarés en ligne (`price_data`) à partir de `billing.PLANS`, qui reste la
   seule source de vérité. Sans ça, la grille existerait à deux endroits et
   finirait par diverger — et il faudrait recréer les produits à la main pour
   passer du mode test au mode réel.

3. **Dégradation propre.** Sans `STRIPE_SECRET_KEY`, les routes de paiement
   répondent 503 avec un message explicite et **tout le reste du produit
   continue de fonctionner**. L'essai de 3 cocons et l'attribution manuelle de
   formule suffisent à faire tourner l'outil sans paiement.

⚠️ **L'idempotence des webhooks ne peut pas venir du ledger.** Stripe livre
« au moins une fois » et rejoue ses événements après un timeout ou une erreur.
Un achat crédité deux fois est un cocon offert. La déduplication se fait sur
`event.id`, dans la table `stripe_events`, et dans la même transaction que
l'octroi — voir `BillingRepository.grant_purchase_from_stripe`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from billing import PLANS, Plan

logger = logging.getLogger(__name__)

# Prix affichés TTC ou HT ? La grille de CLAUDE.md est en euros hors taxes ;
# Stripe Tax, s'il est activé sur le compte, ajoute la TVA par-dessus.
CURRENCY = "eur"

# 20 € le cocon à l'unité. Sert à dérisquer le premier achat : dès 3 cocons,
# l'abonnement Indépendant (49 €) devient plus avantageux, et c'est voulu.
UNIT_PRICE_EUR = 20


class PaymentsNotConfigured(RuntimeError):
    """Stripe n'est pas configuré — remonté en 503 par l'API."""


def is_configured() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY"))


def _secret_key() -> str:
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise PaymentsNotConfigured(
            "Le paiement en ligne n'est pas activé sur ce serveur "
            "(STRIPE_SECRET_KEY absent). Les formules se règlent hors ligne pour "
            "l'instant : contactez-nous et nous créditons votre compte."
        )
    return key


def webhook_secret() -> str:
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise PaymentsNotConfigured(
            "STRIPE_WEBHOOK_SECRET absent — impossible de vérifier la signature "
            "des webhooks. Sans cette vérification, n'importe qui pourrait "
            "créditer un compte en appelant la route."
        )
    return secret


def base_url() -> str:
    """Racine publique du front, pour les URL de retour de Checkout."""
    explicit = os.getenv("PUBLIC_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    domain = os.getenv("DOMAIN")
    return f"https://{domain}" if domain else "http://localhost:3000"


def get_client() -> Any:
    from stripe import StripeClient

    return StripeClient(_secret_key())


@dataclass(frozen=True)
class CheckoutTarget:
    """Ce qui est acheté — un abonnement mensuel ou un lot de cocons."""

    mode: str  # "subscription" | "payment"
    line_item: dict[str, Any]
    metadata: dict[str, str]


def subscription_target(plan: Plan) -> CheckoutTarget:
    if plan.monthly_price_eur <= 0 or plan.cocoons_per_month <= 0:
        raise ValueError(f"La formule « {plan.key} » ne peut pas être souscrite.")
    return CheckoutTarget(
        mode="subscription",
        line_item={
            "price_data": {
                "currency": CURRENCY,
                "unit_amount": plan.monthly_price_eur * 100,
                "recurring": {"interval": "month"},
                "product_data": {
                    "name": f"Cocon Sémantique — {plan.label}",
                    "description": (
                        f"{plan.cocoons_per_month} cocons par mois. Les cocons non "
                        "consommés restent valables le mois suivant."
                    ),
                },
            },
            "quantity": 1,
        },
        metadata={"plan": plan.key},
    )


def purchase_target(cocoons: int) -> CheckoutTarget:
    if cocoons < 1 or cocoons > 50:
        raise ValueError("Entre 1 et 50 cocons par achat.")
    return CheckoutTarget(
        mode="payment",
        line_item={
            "price_data": {
                "currency": CURRENCY,
                "unit_amount": UNIT_PRICE_EUR * 100,
                "product_data": {
                    "name": "Cocon sémantique à l'unité",
                    "description": "1 cocon = 1 article mère + 5 articles filles. N'expire pas.",
                },
            },
            "quantity": cocoons,
        },
        metadata={"cocoons": str(cocoons)},
    )


def plan_from_key(key: str) -> Plan:
    plan = PLANS.get(key)
    if plan is None:
        raise ValueError(
            f"Formule inconnue : « {key} ». Attendu : "
            + ", ".join(k for k, p in PLANS.items() if p.monthly_price_eur > 0)
        )
    return plan


# ============================================================
# Appels Stripe
# ============================================================


def ensure_customer(*, customer_id: str | None, email: str, name: str, agency_id: str) -> str:
    """Client Stripe de l'agence, créé au besoin.

    `agency_id` est posé en métadonnée : si un jour il faut rapprocher les deux
    bases à la main, c'est ce qui rend l'opération possible.
    """
    client = get_client()
    if customer_id:
        try:
            existing = client.v1.customers.retrieve(customer_id)
            if not getattr(existing, "deleted", False):
                return customer_id
            logger.warning("Client Stripe %s supprimé — recréation.", customer_id)
        except Exception as e:
            logger.warning("Client Stripe %s illisible (%s) — recréation.", customer_id, e)

    created = client.v1.customers.create(
        params={"email": email, "name": name, "metadata": {"agency_id": agency_id}}
    )
    return created.id


def create_checkout_session(
    *, customer_id: str, target: CheckoutTarget, agency_id: str
) -> str:
    """URL de la page de paiement Stripe.

    `agency_id` est répété dans les métadonnées de la session ET dans celles de
    l'abonnement : le webhook `checkout.session.completed` et les webhooks
    `customer.subscription.*` sont des événements distincts, et le second ne
    voit pas les métadonnées du premier.
    """
    metadata = {**target.metadata, "agency_id": agency_id}
    params: dict[str, Any] = {
        "mode": target.mode,
        "customer": customer_id,
        "line_items": [target.line_item],
        "success_url": f"{base_url()}/billing?paiement=ok",
        "cancel_url": f"{base_url()}/billing?paiement=annule",
        "client_reference_id": agency_id,
        "metadata": metadata,
    }
    if target.mode == "subscription":
        params["subscription_data"] = {"metadata": metadata}

    session = get_client().v1.checkout.sessions.create(params=params)
    return session.url


def create_portal_session(customer_id: str) -> str:
    """Portail client Stripe — moyens de paiement, factures, résiliation."""
    session = get_client().v1.billing_portal.sessions.create(
        params={"customer": customer_id, "return_url": f"{base_url()}/billing"}
    )
    return session.url


def construct_event(payload: bytes, signature: str) -> dict[str, Any]:
    """Vérifie la signature Stripe et rend l'événement en dict simple.

    Sans cette vérification, la route de webhook serait un moyen public de
    créditer n'importe quel compte : il suffirait d'en connaître l'URL.

    Le retour est converti en `dict` natif plutôt que laissé en `StripeObject` :
    ce dernier redirige les attributs inconnus vers ses données, si bien qu'un
    `obj.get(...)` lève un `KeyError` au lieu d'appeler `dict.get`. Le
    gestionnaire d'événements n'a pas à connaître ce piège.
    """
    import json

    from stripe import Webhook

    event = Webhook.construct_event(payload, signature, webhook_secret())
    # `to_dict()` n'est pas récursif — un aller-retour JSON l'est, et
    # l'événement vient de toute façon d'un corps JSON.
    return json.loads(json.dumps(event.to_dict(), default=str))
