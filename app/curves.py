"""Pure fan-curve decisions. No hardware access."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    percent: float
    reason: str
    write: bool


def interpolate(curve: list[tuple[float, float]], temp: float) -> float:
    if not curve:
        return 100.0
    points = sorted(curve, key=lambda item: item[0])
    if temp <= points[0][0]:
        return points[0][1]
    if temp >= points[-1][0]:
        return points[-1][1]
    for (left_temp, left_pwm), (right_temp, right_pwm) in zip(points, points[1:]):
        if left_temp <= temp <= right_temp:
            span = right_temp - left_temp
            if span <= 0:
                return right_pwm
            mix = (temp - left_temp) / span
            return left_pwm + (right_pwm - left_pwm) * mix
    return points[-1][1]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _slew(current: float, target: float, step: float) -> float:
    if step <= 0:
        return target
    if target > current:
        return min(target, current + step)
    return max(target, current - step)


def decide_pwm(
    *,
    armed: bool,
    enabled: bool,
    mode: str,
    min_percent: float,
    max_percent: float,
    manual_percent: float,
    curve: list[tuple[float, float]],
    temp: float | None,
    temp_state: str,
    hysteresis_c: float,
    anchor_temp: float | None,
    last_output: float | None,
    slew_step: float,
    failsafe_percent: float,
    override: float | None,
    resume_from: float | None,
) -> tuple[Decision, float | None, float | None]:
    """Return the duty decision plus the next anchor temperature and last output.

    ``resume_from`` is the duty a just-finished identify pulse left on the header,
    so the next automatic change slews down from that duty instead of jumping.
    """

    if override is not None and armed:
        duty = _clamp(override, 0, 100)
        return Decision(duty, "identify", True), anchor_temp, last_output

    if resume_from is not None:
        last_output = resume_from

    if not armed or not enabled or mode == "monitor":
        shown = last_output if last_output is not None else 0.0
        return Decision(shown, "monitor", False), anchor_temp, last_output

    if mode == "manual":
        target = _clamp(manual_percent, min_percent, max_percent)
        reason = "manual"
        anchor = anchor_temp
    elif temp_state == "spun_down":
        target = min_percent
        reason = "spun-down"
        anchor = anchor_temp
    elif temp_state != "ok" or temp is None:
        target = max(failsafe_percent, min_percent)
        output = _clamp(target, 0, 100)
        return Decision(output, "failsafe", True), anchor_temp, output
    else:
        ideal = _clamp(interpolate(curve, temp), min_percent, max_percent)
        held = (
            anchor_temp is not None
            and last_output is not None
            and ideal < last_output
            and temp > anchor_temp - max(0.0, hysteresis_c)
        )
        if held:
            target = last_output
            reason = "hold"
            anchor = anchor_temp
        else:
            target = ideal
            reason = "curve"
            anchor = temp

    if last_output is None:
        output = target
    else:
        output = _slew(last_output, target, slew_step)
    output = _clamp(output, 0, 100)
    if mode == "manual" or reason == "spun-down":
        output = _clamp(output, min_percent, max_percent)
    return Decision(output, reason, True), anchor, output


def mix_color(stops: list[tuple[float, tuple[int, int, int]]], temp: float) -> tuple[int, int, int]:
    if not stops:
        return (0, 0, 0)
    points = sorted(stops, key=lambda item: item[0])
    if temp <= points[0][0]:
        return points[0][1]
    if temp >= points[-1][0]:
        return points[-1][1]
    for (left_temp, left), (right_temp, right) in zip(points, points[1:]):
        if left_temp <= temp <= right_temp:
            span = right_temp - left_temp
            mix = 0.0 if span <= 0 else (temp - left_temp) / span
            return tuple(int(round(a + (b - a) * mix)) for a, b in zip(left, right))
    return points[-1][1]
