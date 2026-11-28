"""Contract tests of the tester's server-side message catalog."""

from __future__ import annotations

import re

from demo.web.tester.messages import MESSAGE_CATALOG
from demo.web.tester.messages import TesterMessages as Messages
from shared.run_constants import LANGUAGE_EN, LANGUAGE_ZH

_CJK = re.compile(r"[一-鿿]")


def test_both_languages_define_the_same_message_keys() -> None:
    zh = MESSAGE_CATALOG[LANGUAGE_ZH]
    en = MESSAGE_CATALOG[LANGUAGE_EN]

    assert set(en) == set(zh)


def test_every_english_message_is_english() -> None:
    for key, value in MESSAGE_CATALOG[LANGUAGE_EN].items():
        assert not _CJK.search(value), f"en-US {key} still contains Chinese"


def test_an_unknown_language_falls_back_to_chinese() -> None:
    messages = Messages("fr-FR")

    assert messages.language == LANGUAGE_ZH
    assert messages.get("unknown_batch") == MESSAGE_CATALOG[LANGUAGE_ZH]["unknown_batch"]


def test_messages_render_in_both_languages() -> None:
    assert Messages(LANGUAGE_ZH).get("unknown_batch") != ""
    assert Messages(LANGUAGE_EN).get("unknown_batch") != ""
    assert Messages(LANGUAGE_ZH).get("unknown_batch") != Messages(LANGUAGE_EN).get("unknown_batch")


def test_parameters_are_interpolated_and_never_left_as_braces() -> None:
    messages = Messages(LANGUAGE_EN)

    rendered = messages.get("followup_not_sent", final_state="TASK_STATE_FAILED")

    assert "TASK_STATE_FAILED" in rendered
    assert "{" not in rendered and "}" not in rendered


def test_an_unknown_key_stays_visible() -> None:
    assert Messages(LANGUAGE_EN).get("no_such_key") == "no_such_key"
