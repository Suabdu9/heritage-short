"""Fast unit tests: contract, cache keys, consent gate, subtitles, timeline."""
from __future__ import annotations

import copy
import json

import pytest

from pipeline import common
from pipeline.assemble import _chunks, build_timeline
from pipeline.narration import narration_key
from pipeline.shots import clip_key, video_settings

SB = common.load_storyboard(common.ROOT / "storyboard.json")


def test_storyboard_is_valid():
    assert 8 <= len(SB["shots"]) <= 12
    assert all(s["reason"] for s in SB["shots"])


def test_schema_rejects_missing_field(tmp_path):
    bad = copy.deepcopy(SB)
    del bad["shots"][0]["reason"]
    p = tmp_path / "sb.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(common.StoryboardError, match="reason"):
        common.load_storyboard(p)


def test_semantic_check_catches_unknown_character(tmp_path):
    bad = copy.deepcopy(SB)
    bad["shots"][1]["image_prompt"] = "{grandma} walking"
    p = tmp_path / "sb.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(common.StoryboardError, match="grandma"):
        common.load_storyboard(p)


def test_editing_one_line_changes_only_that_key():
    before = {s["id"]: narration_key(SB, s, "addis", "am-yohanes-calm") for s in SB["shots"]}
    sb2 = copy.deepcopy(SB)
    sb2["shots"][2]["narration"] += " ።"
    after = {s["id"]: narration_key(sb2, s, "addis", "am-yohanes-calm") for s in sb2["shots"]}
    changed = [k for k in before if before[k] != after[k]]
    assert changed == [SB["shots"][2]["id"]]


def test_clip_key_depends_on_keyframe_and_motion():
    st = video_settings(SB)
    s = SB["shots"][0]
    k = clip_key("aaaa", s, st)
    assert k == clip_key("aaaa", s, st)
    assert k != clip_key("bbbb", s, st)
    s2 = dict(s, motion_prompt="camera static")
    assert k != clip_key("aaaa", s2, st)


def test_prompt_expansion_reuses_fixed_description():
    shot = next(s for s in SB["shots"] if "{mother}" in s["image_prompt"])
    p = common.expand_prompt(SB, shot["image_prompt"])
    assert SB["characters"][0]["description"] in p
    assert SB["style"]["block"] in p
    assert "{" not in p


def test_consent_gate_blocks_without_record(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CONSENT", tmp_path / "consent.json")
    with pytest.raises(common.ConsentError):
        common.require_consent(SB, ["mother"], "likeness")
    (tmp_path / "consent.json").write_text(json.dumps({"mother": {"granted": True, "scope": ["likeness"]}}))
    common.require_consent(SB, ["mother"], "likeness")
    with pytest.raises(common.ConsentError):
        common.require_consent(SB, ["mother"], "story")


def test_subtitle_chunks_fit_two_lines():
    long = SB["shots"][8]["subtitle_en"]
    for cue in _chunks(long, 30):
        lines = cue.split("\n")
        assert len(lines) <= 2 and all(len(l) <= 30 for l in lines)
    assert " ".join(c.replace("\n", " ") for c in _chunks(long, 30)) == long


def test_timeline_stretches_to_fit_voice(monkeypatch):
    fake = {s["id"]: {"file": "x.wav", "duration_s": 9.0} if s["narration"] else {"file": None, "duration_s": 0} for s in SB["shots"]}
    monkeypatch.setattr("pipeline.assemble.read_current", lambda stage: fake)
    tl = build_timeline(SB)
    for plan, shot in zip(tl["shots"], SB["shots"]):
        assert plan["length"] >= shot["duration_s"]
        if shot["narration"]:
            assert plan["voice_start"] + plan["voice_dur"] <= plan["start"] + plan["length"]
