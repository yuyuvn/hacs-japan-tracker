"""Constants for Japan Parcel Tracker."""

DOMAIN = "japan_tracker"

CONF_TODO_ENTITY = "todo_entity"
CONF_SCAN_INTERVAL = "scan_interval"  # minutes
CONF_AUTO_COMPLETE = "auto_complete"  # tick delivered items as done
CONF_WRITE_STATUS = "write_status"  # write latest status into item description

DEFAULT_SCAN_INTERVAL = 60

CARRIER_SAGAWA = "sagawa"
CARRIER_KURONEKO = "kuroneko"
CARRIER_CAINIAO = "cainiao"
CARRIER_JAPANPOST = "japanpost"
CARRIERS = {
    CARRIER_SAGAWA: "Sagawa Express (佐川急便)",
    CARRIER_KURONEKO: "Kuroneko Yamato (ヤマト運輸)",
    CARRIER_JAPANPOST: "Japan Post (日本郵便)",
    CARRIER_CAINIAO: "Cainiao (AliExpress)",
}

CARD_URL = "/japan_tracker_static/japan-tracker-card.js"
