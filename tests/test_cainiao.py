"""Offline tests for the Cainiao parser (dummy payloads)."""
from custom_components.japan_tracker.carriers import cainiao
from custom_components.japan_tracker.carriers.base import STATUS_OUT_FOR_DELIVERY


def test_japanese_out_for_delivery_text() -> None:
    """Last-mile Japanese text such as 配達中 means out for delivery."""
    mod = {
        "mailNo": "LP00000000000000",
        "status": "DELIVERING",
        "detailList": [  # newest first
            {"desc": "配達中", "actionCode": "CC_UPDATE", "timeStr": "2026-01-02 09:00:00"},
            {"desc": "Arrived at destination country", "actionCode": "CC_HI_START", "timeStr": "2026-01-01 10:00:00"},
        ],
        "processInfo": {"progressPointList": [
            {"pointName": "Mainland China", "light": True},
            {"pointName": "Destination city", "light": True},
            {"pointName": "Delivered", "light": False},
        ]},
    }
    result = cainiao.parse_module(mod)
    assert result.status == STATUS_OUT_FOR_DELIVERY
    assert result.status_text == "配達中 (Destination city)"
