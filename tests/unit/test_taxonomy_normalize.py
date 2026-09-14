from indian_mf_mcp.normalize.taxonomy import (
    is_segregated_portfolio_name,
    parse_plan_option,
    parse_plan_option_columns,
)


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


def test_plan_option_columns_direct_growth():
    info = parse_plan_option_columns("Direct Plan", "Growth Option")
    assert (info.plan_type, info.option_type, info.idcw_variant) == ("Direct", "Growth", None)


def test_plan_option_columns_idcw_variants():
    payout = parse_plan_option_columns("Regular Plan", "IDCW Payout Option")
    assert (payout.plan_type, payout.option_type, payout.idcw_variant) == ("Regular", "IDCW", "Payout")

    reinvest = parse_plan_option_columns("Direct Plan", "Monthly IDCW Reinvestment")
    assert (reinvest.option_type, reinvest.idcw_variant) == ("IDCW", "Reinvest")

    spelled_out = parse_plan_option_columns("Direct Plan", "Income Distribution cum capital withdrawal")
    assert spelled_out.option_type == "IDCW"


def test_plan_option_columns_blank_returns_none():
    assert parse_plan_option_columns(None, None) is None
    assert parse_plan_option_columns("", "  ") is None


def test_plan_option_columns_unknown_option_is_none_not_guessed():
    info = parse_plan_option_columns("Direct Plan", "Bonus Option")
    assert info.plan_type == "Direct"
    assert info.option_type is None


# ---------------------------------------------------------------------------
# Segregated-portfolio name detection (spec §9.2)
# ---------------------------------------------------------------------------

def test_segregated_portfolio_detected():
    assert is_segregated_portfolio_name(
        "Franklin India Ultra Short Bond Fund - Segregated Portfolio 1 - Direct Plan - Growth"
    ) is True
    assert is_segregated_portfolio_name(
        "UTI Credit Risk Fund-Segregated Portfolio - 1 -Regular Plan-Growth"
    ) is True


def test_segregated_portfolio_abbreviated_form_detected():
    assert is_segregated_portfolio_name("HDFC Credit Risk Debt Fund - Seg Portfolio 2") is True
    assert is_segregated_portfolio_name("HDFC Credit Risk Debt Fund - Seg. Portfolio 2") is True


def test_ordinary_scheme_name_not_flagged():
    assert is_segregated_portfolio_name("Parag Parikh Flexi Cap Fund - Direct Plan - Growth") is False


def test_segregated_portfolio_handles_missing_name():
    assert is_segregated_portfolio_name(None) is False
    assert is_segregated_portfolio_name("") is False
