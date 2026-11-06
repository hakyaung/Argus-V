"""FakeWeb research fixtures AV-S61..AV-S70."""
from .fakeweb_profiles import ITEMS, make_profiles

PROFILES = make_profiles([item for item in ITEMS if 61 <= item[0] <= 70])
