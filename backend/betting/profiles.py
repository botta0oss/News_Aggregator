"""Risk presets for the simulated portfolio."""
from dataclasses import dataclass, asdict

from backend.i18n import lang


@dataclass(frozen=True)
class RiskProfile:
    key: str
    label: str
    description: str
    kelly_scale: float        # fraction of the (book-aware) Kelly stake
    z: float                  # how many standard deviations of model uncertainty to subtract
    min_net_edge: float       # edge after spread, fees and uncertainty, in probability points
    min_apr_premium: float    # required annualized return above the risk-free rate
    max_market_frac: float    # max share of capital in one market
    max_event_frac: float     # ... in one event (markets on the same event are correlated)
    max_category_frac: float  # ... in one category (e.g. all crypto markets)
    max_total_frac: float     # max share of capital invested at the same time
    max_book_share: float     # max share of the visible order-book depth we would take
    min_liquidity: float      # skip markets with less liquidity (USD)
    max_days: int             # skip markets resolving further away than this
    min_hours_to_end: float = 24.0  # skip markets resolving sooner: the price already knows the outcome
    min_roi: float = 0.06     # minimum prudent expected return on the outlay, per bet (not annualized)

    def as_dict(self) -> dict:
        out = asdict(self)
        if lang() != "it" and self.key in EN:
            out["label"], out["description"] = EN[self.key]
        return out


# The presets in English (the Italian text is in PROFILES)
EN = {
    "prudente": ("Prudent", "Few, small bets, only with a wide margin and liquid markets that close within 4 months."),
    "bilanciato": ("Balanced", "The default compromise: a quarter of Kelly, a 3-point margin after costs, at most 4% of capital per market."),
    "aggressivo": ("Aggressive", "More and bigger bets: half Kelly, smaller margins, far-off markets too. Wide capital swings."),
}


PROFILES = {
    "prudente": RiskProfile(
        key="prudente", label="Prudente",
        description="Poche scommesse, piccole, solo con margine ampio e mercati liquidi che si chiudono entro 4 mesi.",
        kelly_scale=0.15, z=1.64, min_net_edge=0.04, min_apr_premium=0.15,
        max_market_frac=0.02, max_event_frac=0.05, max_category_frac=0.15, max_total_frac=0.40,
        max_book_share=0.10, min_liquidity=25_000, max_days=120, min_hours_to_end=72, min_roi=0.1,
    ),
    "bilanciato": RiskProfile(
        key="bilanciato", label="Bilanciato",
        description="Il compromesso di default: un quarto di Kelly, margine di 3 punti dopo i costi, massimo 4% del capitale per mercato.",
        kelly_scale=0.25, z=1.0, min_net_edge=0.03, min_apr_premium=0.08,
        max_market_frac=0.04, max_event_frac=0.08, max_category_frac=0.25, max_total_frac=0.60,
        max_book_share=0.20, min_liquidity=10_000, max_days=365, min_hours_to_end=24, min_roi=0.06,
    ),
    "aggressivo": RiskProfile(
        key="aggressivo", label="Aggressivo",
        description="Più scommesse e più grandi: mezzo Kelly, margini ridotti, anche mercati lontani. Oscillazioni del capitale ampie.",
        kelly_scale=0.5, z=0.5, min_net_edge=0.02, min_apr_premium=0.03,
        max_market_frac=0.08, max_event_frac=0.15, max_category_frac=0.40, max_total_frac=0.85,
        max_book_share=0.30, min_liquidity=5_000, max_days=730, min_hours_to_end=12, min_roi=0.03,
    ),
}


def get_profile(key: str) -> RiskProfile:
    return PROFILES.get(key, PROFILES["bilanciato"])
