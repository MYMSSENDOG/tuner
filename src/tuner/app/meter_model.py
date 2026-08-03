"""Pure display logic for the cent meter (no Qt imports, unit-testable)."""

from __future__ import annotations

GREEN_LIMIT = 8.0
ORANGE_LIMIT = 15.0

METER_RANGE = 50.0  # meter shows -50 .. +50 cents
METER_SWEEP_DEG = 100.0  # total needle sweep angle


def zone_for_cents(cents: float) -> str:
    magnitude = abs(cents)
    if magnitude <= GREEN_LIMIT:
        return "green"
    if magnitude <= ORANGE_LIMIT:
        return "orange"
    return "red"


def needle_angle_deg(cents: float) -> float:
    """Angle from vertical, negative = flat (left). Clamped to the meter range."""
    clamped = max(-METER_RANGE, min(METER_RANGE, cents))
    return clamped / METER_RANGE * (METER_SWEEP_DEG / 2.0)
