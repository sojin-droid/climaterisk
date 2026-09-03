"""Controlled vocabularies for the climate-risk domain.

These string enums are the single source of truth for the dimensional axes
(depth, sector, geographic scale) and the hazard/scenario taxonomies. The
bundled library JSON in ``assets/libraries/`` keys off these same values.
"""

from __future__ import annotations

from enum import StrEnum


class DepthLevel(StrEnum):
    """Aggregation depth — the platform's primary build ladder."""

    ASSET = "asset"
    PORTFOLIO = "portfolio"
    NATIONAL = "national"


class GeographicScale(StrEnum):
    """Spatial footprint of an asset — drives exposure representation + hazard resolution."""

    POINT = "point"  # a single coordinate (building, plant)
    FOOTPRINT = "footprint"  # a polygon (campus, large site)
    REGIONAL = "regional"  # an administrative region
    NATIONAL = "national"  # a whole country


class Sector(StrEnum):
    """Economic sector — selects vulnerability + transition-exposure profiles.

    Must stay in sync with ``assets/libraries/sectors.json``.
    """

    STEEL = "steel"
    PETROCHEMICAL = "petrochemical"
    CHEMICALS = "chemicals"
    CEMENT = "cement"
    GLASS_CERAMICS = "glass_ceramics"
    ALUMINIUM = "aluminium"
    MINING = "mining"
    OIL_GAS = "oil_gas"
    UTILITIES = "utilities"
    WATER_WASTE = "water_waste"
    PULP_PAPER = "pulp_paper"
    AUTOMOTIVE = "automotive"
    ELECTRONICS = "electronics"
    PHARMACEUTICALS = "pharmaceuticals"
    FOOD_BEVERAGE = "food_beverage"
    TEXTILES = "textiles"
    CONSTRUCTION = "construction"
    AGRICULTURE = "agriculture"
    AVIATION = "aviation"
    SHIPPING = "shipping"
    TRANSPORT_ROAD = "transport_road"
    RAIL = "rail"
    DATA_CENTERS = "data_centers"
    TELECOM = "telecom"
    RETAIL = "retail"
    HOSPITALITY = "hospitality"
    HEALTHCARE = "healthcare"
    FINANCIAL = "financial"
    REAL_ESTATE = "real_estate"


class VulnerabilityClass(StrEnum):
    """Physical vulnerability class — maps to a per-peril damage curve set."""

    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    INDUSTRIAL_LIGHT = "industrial_light"
    INDUSTRIAL_HEAVY = "industrial_heavy"
    INFRASTRUCTURE = "infrastructure"


class ExposureSource(StrEnum):
    """How a run's exposure is built (drives which CLIMADA exposure builder is used)."""

    POINTS = "points"  # user-placed map points (default)
    LITPOP = "litpop"  # modeled LitPop national grid (nightlight × population)
    OSM = "osm"  # OpenStreetMap / building footprints (climada_petals osm-flex)
    CROP = "crop"  # crop-production exposure (ISIMIP, for agricultural perils)


class Peril(StrEnum):
    """Physical climate hazards. Values align with CLIMADA / climada_petals hazard types."""

    TROPICAL_CYCLONE = "tropical_cyclone"
    RIVER_FLOOD = "river_flood"
    WILDFIRE = "wildfire"
    EUROPEAN_WINDSTORM = "european_windstorm"
    EARTHQUAKE = "earthquake"
    COASTAL_FLOOD = "coastal_flood"
    HEATWAVE = "heatwave"
    HEAT_MORTALITY = "heat_mortality"  # heat-attributable deaths (health, not currency)
    DROUGHT = "drought"
    HAIL = "hail"
    TC_RAIN = "tc_rain"
    TC_SURGE = "tc_surge"
    LANDSLIDE = "landslide"
    LOW_FLOW = "low_flow"
    CROP_YIELD = "crop_yield"


class ClimateScenario(StrEnum):
    """Physical climate forcing pathways (RCP family; SSP aliases handled in library)."""

    RCP26 = "rcp26"
    RCP45 = "rcp45"
    RCP60 = "rcp60"
    RCP85 = "rcp85"


class TransitionScenario(StrEnum):
    """NGFS Phase-5 transition scenarios (MVP subset)."""

    NET_ZERO_2050 = "net_zero_2050"
    BELOW_2C = "below_2c"
    DELAYED_TRANSITION = "delayed_transition"
    CURRENT_POLICIES = "current_policies"
