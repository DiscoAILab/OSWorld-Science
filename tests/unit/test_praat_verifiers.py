"""The Praat evaluator dispatches to the vendored author verifier a task names."""
import pytest

from osworld_science.evaluators.linguistics import VERIFIERS, _verifier, praat_vot_textgrid


def test_both_verifiers_load_with_their_own_tables():
    pos, neg = _verifier("praat_vot_verifier"), _verifier("praat_vot_neg_verifier")
    assert set(pos.TOLERANCES_SECONDS) == {"bat", "Pat", "bit", "pit"}
    assert neg.TOLERANCES_SECONDS == {"bun": 0.005} and neg.VOT_SIGN == "negative"
    assert not hasattr(pos, "VOT_SIGN")
    assert set(VERIFIERS) == {"praat_vot_verifier", "praat_vot_neg_verifier"}


def test_unknown_verifier_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown Praat verifier"):
        praat_vot_textgrid(tmp_path, {"file": "x.TextGrid", "verifier": "nope"}, {}, None)


def test_missing_submission_reports_cleanly(tmp_path):
    score, detail = praat_vot_textgrid(tmp_path, {"file": "x.TextGrid", "verifier": "praat_vot_neg_verifier"}, {}, None)
    assert score == 0.0 and "missing file" in detail["error"]
