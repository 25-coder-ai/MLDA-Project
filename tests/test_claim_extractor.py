from src.verification.claim_extractor import counterfactual, extract_entities, parse_review_text
from src.verification.semantic_checker import CORRECTNESS_POS, detect_polarity


def test_parse_structured_review_lines():
    claims = parse_review_text(
        "[C1] shop/db.py:20-23 (Security) `find_user` builds SQL with an f-string.\n"
        "  continuation of C1.\n"
        "- shop/pricing.py#L27 [Bug]: split_bill divides by `people`\n"
    )
    assert [c.claim_id for c in claims] == ["C1", "C2"]
    assert (claims[0].target_file, claims[0].target_line_start, claims[0].target_line_end) == ("shop/db.py", 20, 23)
    assert claims[0].claimed_defect_category == "Security"
    assert claims[0].claim_text.endswith("continuation of C1.")
    assert claims[1].target_line_start == 27 and claims[1].claimed_defect_category == "Bug"


def test_free_text_becomes_inferred_claim():
    c = parse_review_text("The function `apply_discount` is correct.")[0]
    assert c.source == "inferred" and c.target_file == "" and c.claimed_defect_category == "Correctness"


def test_entity_extraction():
    e = extract_entities("In `reserve`, the parameter `quantity_requested` calls pricing.round_currency() — see REQ-042; O(n^2) is fine.")
    assert "reserve" in e.tokens and "pricing.round_currency" in e.tokens
    assert e.parameters == ["quantity_requested"]
    assert e.requirement_ids == ["REQ-042"]
    assert "O" not in e.tokens


def test_polarity_handles_negated_positive_phrase():
    from src.verification.semantic_checker import PREDICATES

    spec = next(p for p in PREDICATES if p.kind == "checks_none")
    assert detect_polarity("`authenticate` never checks for None", spec.pos, spec.neg)[0] is False
    assert detect_polarity("`authenticate` checks for None first", spec.pos, spec.neg)[0] is True
    assert detect_polarity("`f` is provably correct", CORRECTNESS_POS, [])[0] is True


def test_counterfactual_negates():
    assert counterfactual("`x` does not handle errors") == "`x` does handle errors"
    assert "not" in counterfactual("`f` is recursive").lower()
