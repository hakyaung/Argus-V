"""FakeWeb research fixtures AV-S71..AV-S80."""
from .fakeweb_profiles import ITEMS, make_profiles

PROFILES = make_profiles([item for item in ITEMS if 71 <= item[0] <= 80])
