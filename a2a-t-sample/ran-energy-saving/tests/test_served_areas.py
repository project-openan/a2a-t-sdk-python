"""Tests of the served-area catalog and its fuzzy matching."""

from __future__ import annotations

from demo.served_areas import (
    RECOMMENDATION_THRESHOLD,
    SERVED_AREAS,
    build_area_options,
    build_area_proposal_text,
    match_served_areas,
    normalize_area,
    recommended_area,
    resolve_served_area,
)


def test_every_served_area_is_resolved_by_its_names_and_aliases() -> None:
    for area in SERVED_AREAS:
        for name in area.names:
            assert resolve_served_area(name) == area


def test_normalization_removes_area_suffixes_and_case() -> None:
    assert normalize_area("  Songshanhu  AREA ") == "songshanhu"
    assert normalize_area("松山湖区域") == "松山湖"
    assert normalize_area("未知区域") == "未知"


def test_unknown_area_lists_every_served_area() -> None:
    matches = match_served_areas("Region Z")

    assert [match.area.area_id for match in matches] == ["songshanhu", "dalingshan"]
    assert recommended_area(matches) is None
    proposal = build_area_proposal_text("Region Z", matches, "en-US")
    assert "Songshanhu Administration Committee" in proposal
    assert "Dalingshan" in proposal
    assert "No close match" in proposal


def test_a_near_miss_is_recommended_but_still_needs_confirmation() -> None:
    matches = match_served_areas("Songshanhu Admin Commitee")

    assert matches[0].area.area_id == "songshanhu"
    assert matches[0].score >= RECOMMENDATION_THRESHOLD
    assert recommended_area(matches) == matches[0].area
    proposal = build_area_proposal_text("Songshanhu Admin Commitee", matches, "en-US")
    assert "Closest match: Songshanhu Administration Committee" in proposal


def test_chinese_request_renders_the_localized_area_list() -> None:
    matches = match_served_areas("未知区域")
    proposal = build_area_proposal_text("未知区域", matches, "zh-CN")

    assert "松山湖管委会" in proposal
    assert "大岭山" in proposal
    assert "未找到相近区域" in proposal


def test_options_payload_marks_the_recommended_area() -> None:
    matches = match_served_areas("大岭山管委")
    payload = build_area_options("大岭山管委", matches)
    options = payload["options"]

    assert payload["field"] == "region"
    assert payload["requested"] == "大岭山管委"
    assert isinstance(options, list)
    selected = [option for option in options if option["recommended"]]
    assert len(selected) == 1
    assert selected[0]["labels"]["zh-CN"] == "大岭山"
