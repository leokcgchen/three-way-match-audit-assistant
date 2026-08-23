from __future__ import annotations

SCHEMA_VERSION = "trading-model-artifact/v1"
DICT_VERSION = "1.1"
PROMPT_VERSION = "trading_model_v1"
GATE_VERSION = "1.0"

INCOTERM_CODES = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")
SEA_ONLY = frozenset({"FAS", "FOB", "CFR", "CIF"})

EVENT_TO_CANDIDATE = {
    "before_carriage": ("outbound", "cargo_ready"),
    "at_carrier_handover": ("carrier_received",),
    "at_on_board": ("on_board",),
    "at_destination_arrival": ("destination_arrived",),
    "at_customer_receipt": ("customer_receipt",),
    "at_final_acceptance": ("final_acceptance",),
}

CONTROL_ELIGIBLE_EVENTS = {
    "contract_effective",
    "cargo_ready",
    "outbound",
    "carrier_received",
    "on_board",
    "customs_release",
    "destination_arrived",
    "unloaded",
    "customer_receipt",
    "quantity_check",
    "quality_acceptance",
    "final_acceptance",
}

LOW_CONFIDENCE = 0.90
