"""Ajustes compartilhados pelo terminal e pelo painel, preservando as cores."""
import copy
from core import validate_config

SERVO_FIELDS = ("travel_s", "advance_s", "hold_after_arrival_s", "return_s", "rest_us", "push_us")


def changed_config(cfg, changes):
    updated = copy.deepcopy(cfg)
    if not isinstance(changes, dict) or not set(changes) <= {"roi", "servos"}:
        raise ValueError("Ajuste aceita somente roi e servos")
    if "roi" in changes:
        updated["vision"]["roi"] = changes["roi"]
    servos = changes.get("servos", {})
    if not isinstance(servos, dict) or not set(servos) <= set(updated["servos"]):
        raise ValueError("Servo inválido")
    for side, fields in servos.items():
        if not isinstance(fields, dict) or not set(fields) <= set(SERVO_FIELDS):
            raise ValueError("Parâmetro de servo inválido")
        updated["servos"][side].update(fields)
    return validate_config(updated)
