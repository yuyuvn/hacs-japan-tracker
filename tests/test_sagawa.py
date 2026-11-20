"""Offline tests for the Sagawa parser (dummy pages mimicking k2k.sagawa-exp.co.jp)."""
import pytest

from custom_components.japan_tracker.carriers import sagawa
from custom_components.japan_tracker.carriers.base import (
    STATUS_DELIVERED,
    STATUS_INFO_RECEIVED,
    STATUS_NOT_FOUND,
    CarrierError,
)


def _entry(i: int, number: str, state: str, message: str, info: str, rows: str, history: str) -> str:
    return f"""
<dt id="list{i}"><div class="table_okurijo_index"><table class="table_basic ttl02"><tbody>
  <tr><th class="detail"><span class="detail_open">詳細{i}</span></th>
      <th class="number nowrap"><strong>{number}</strong></th>
      <th><span class="state">{state}</span></th></tr>
  <tr><td colspan="3">{message}<br/><dl class="okurijo_info">{info}</dl></td></tr>
</tbody></table></div></dt>
<dd id="detail{i}">
  <table class="table_basic table_okurijo_detail2"><tbody>
    <tr><th class="cell-w">お問い合せ送り状No.</th><td>{number}</td></tr>
    {rows}
  </tbody></table>
  <table class="table_basic table_okurijo_detail2"><tbody>{history}</tbody></table>
</dd>"""


HISTORY_HEAD = "<tr><th class='cell-w'>荷物状況</th><th>日時</th><th>担当営業所</th></tr>"
OFFICE_ROWS = """
<tr><th>出荷日</th><td>2026年01月01日</td></tr>
<tr><th>集荷に関するお問い合せ</th><td><span class="eigyousyo">テスト集荷営業所</span>
  <span class="eigyousyo">TEL:0000-00-0001</span>FAX:00-0000-0001</td></tr>
<tr><th>配達に関するお問い合せ</th><td><span class="eigyousyo">テスト配達営業所</span>
  <span class="eigyousyo">TEL:0000-00-0002</span>FAX:00-0000-0002</td></tr>
<tr><th>お荷物個数</th><td>1個</td></tr>
"""

PAGE = (
    '<html><body><form id="1"><dl class="toggle">'
    + _entry(
        1, "111122223333", "集荷", "出荷人様よりお荷物をお預かりいたしました。", "", OFFICE_ROWS,
        HISTORY_HEAD + "<tr><td>⇒集荷</td><td>01/01 10:00</td><td>テスト集荷営業所</td></tr>",
    )
    + _entry(
        2, "444455556666", "配達完了", "お荷物のお届けが完了いたしました。",
        "<dt>配達完了日：</dt><dd>01月02日 12:00</dd>", OFFICE_ROWS,
        HISTORY_HEAD
        + "<tr><td>↓集荷</td><td>01/01 10:00</td><td>テスト集荷営業所</td></tr>"
        + "<tr><td>⇒配達完了</td><td>01/02 12:00</td><td>テスト配達営業所</td></tr>",
    )
    + _entry(
        3, "777788889999", "該当なし", "恐れ入りますが、お問い合せ送り状No.をお確かめください。", "",
        "<tr><th>出荷日</th><td></td></tr>", "",
    )
    + "</dl></form></body></html>"
)


def test_parse_results() -> None:
    results = sagawa.parse_results(PAGE)
    assert set(results) == {"111122223333", "444455556666", "777788889999"}

    picked = results["111122223333"]
    assert picked.status == STATUS_INFO_RECEIVED
    assert picked.status_text == "集荷 – 出荷人様よりお荷物をお預かりいたしました。"
    assert picked.events == [{"description": "集荷", "time": "01/01 10:00", "location": "テスト集荷営業所"}]
    assert picked.extra == {
        "shipped_date": "2026年01月01日",
        "package_count": "1個",
        "pickup_office": "テスト集荷営業所",
        "delivery_office": "テスト配達営業所",
        "delivery_office_tel": "0000-00-0002",
        "delivery_dates": [],
    }

    delivered = results["444455556666"]
    assert delivered.status == STATUS_DELIVERED
    assert [e["description"] for e in delivered.events] == ["集荷", "配達完了"]
    assert delivered.extra["delivery_dates"] == ["配達完了日: 01月02日 12:00"]

    assert results["777788889999"].status == STATUS_NOT_FOUND


def test_busy_page_is_an_error() -> None:
    busy = "<html><body>只今、当サービスは大変混み合っており、繋がりにくくなっております。</body></html>"
    with pytest.raises(CarrierError, match="busy"):
        sagawa.parse_results(busy)
    with pytest.raises(CarrierError, match="busy"):
        sagawa._view_state(busy)


def test_view_state() -> None:
    html = '<form><input type="hidden" name="javax.faces.ViewState" id="javax.faces.ViewState" value="abc+/="/></form>'
    assert sagawa._view_state(html) == "abc+/="
