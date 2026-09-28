from osworld_science.evaluators.core import (
    compare_scalar,
    index_columns,
    normalise_key,
    summarise,
    walk_spec,
)


def test_exact_and_abs():
    assert compare_scalar({"expect": 3}, "3.0")[0]
    assert not compare_scalar({"expect": 3}, "3.1")[0]
    assert compare_scalar({"expect": 1.0, "tol_abs": 0.01}, 1.005)[0]
    assert not compare_scalar({"expect": 1.0, "tol_abs": 0.01}, 1.02)[0]


def test_rel_with_floor():
    assert compare_scalar({"expect": 1e-9, "tol_rel": 1e-6, "floor": 1e-8}, 5e-9)[0]  # inside floor
    assert compare_scalar({"expect": 100.0, "tol_rel": 1e-6}, 100.00005)[0]
    assert not compare_scalar({"expect": 100.0, "tol_rel": 1e-6}, 100.1)[0]
    assert not compare_scalar({"expect": 0.0, "tol_rel": 1e-6}, 0.5)[0]  # expected 0, no floor


def test_pvalue_either_band():
    spec = {"expect": 0.00470515, "tol_rel": 1e-4, "tol_abs": 5e-5, "floor": 1e-12}
    assert compare_scalar(spec, 0.0047)[0]           # four decimals read off printed output
    assert not compare_scalar(spec, 0.0049)[0]


def test_strings_and_nulls():
    assert compare_scalar({"expect": "none", "match": "exact_string"}, "none")[0]
    assert compare_scalar({"expect": None}, "NA")[0]
    assert not compare_scalar({"expect": None}, "0")[0]
    assert compare_scalar({"expect": "A"}, " A ")[0]
    assert not compare_scalar({"expect": 2.0, "tol_abs": 0}, "n/a")[0]


def test_walk_spec_and_summarise():
    spec = {"a": {"expect": 1, "tol_abs": 0}, "b": {"c": {"expect": "x"}}, "_note": "ignored"}
    res = walk_spec(spec, {"a": 1, "b": {"c": "y"}})
    score, detail = summarise(res)
    assert score == 0.5 and detail["checked"] == 2 and detail["failures"][0]["field"] == "b.c"


def test_key_normalisation():
    assert normalise_key("Score_A") == normalise_key("scorea") == "scorea"
    assert index_columns(["PATNO", "Score A"]) == {"patno": "PATNO", "scorea": "Score A"}
