from indian_mf_mcp.normalize.taxonomy import parse_plan_option


def test_direct_growth():
    info = parse_plan_option("Parag Parikh Flexi Cap Fund - Direct Plan - Growth")
    assert info.plan_type == "Direct"
    assert info.option_type == "Growth"
    assert info.idcw_variant is None
    assert info.base_scheme_name == "Parag Parikh Flexi Cap Fund"


def test_regular_idcw_reinvest():
    info = parse_plan_option("HDFC Flexi Cap Fund - Regular Plan - IDCW Reinvestment")
    assert info.plan_type == "Regular"
    assert info.option_type == "IDCW"
    assert info.idcw_variant == "Reinvest"


def test_same_base_name_for_all_plan_variants():
    a = parse_plan_option("Axis Midcap Fund - Direct Plan - Growth")
    b = parse_plan_option("Axis Midcap Fund - Regular Plan - Growth")
    assert a.base_scheme_name == b.base_scheme_name
