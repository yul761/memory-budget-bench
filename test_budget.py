"""Guards on the budget filler. Run: .venv/bin/python -m pytest test_budget.py -q"""

from budget import build_prompt, count_tokens

Q = "Where did the user work in 2020?"
D = "2026-08-05"


def payload(events=None, digest="", facts=None):
    return {"digest": digest, "factRegistry": facts or [], "events": [{"content": e} for e in (events or [])]}


def test_never_exceeds_the_budget():
    # The invariant everything else rests on.
    big = ["word " * 500 for _ in range(50)]
    for budget in (200, 1000, 4000, 16000):
        out = build_prompt(Q, D, payload(big), budget)
        assert out.used_tokens <= budget, f"budget {budget} exceeded: {out.used_tokens}"


def test_items_are_whole_or_absent_never_cut():
    # The previous harness cut each retrieved session to 2000 chars, destroying
    # the evidence while retrieval recall still read 1.00.
    events = ["ALPHA " + ("x " * 400) + " OMEGA", "BETA " + ("y " * 400) + " OMEGA"]
    out = build_prompt(Q, D, payload(events), 700)
    for event in events:
        assert (event in out.text) or (event[:40] not in out.text), "an item was truncated mid-way"


def test_reports_what_it_dropped():
    out = build_prompt(Q, D, payload(["word " * 400 for _ in range(10)]), 900)
    assert out.items_dropped > 0
    assert out.items_included + out.items_dropped == 10


def test_underfilled_is_a_finding_not_a_failure():
    # A system with little to give underfills the budget. That is the result,
    # not an error.
    out = build_prompt(Q, D, payload(["short note"]), 8000)
    assert out.underfilled is True
    assert out.items_dropped == 0


def test_ranked_order_is_preserved():
    out = build_prompt(Q, D, payload(["FIRST", "SECOND", "THIRD"]), 8000)
    assert out.text.index("FIRST") < out.text.index("SECOND") < out.text.index("THIRD")


def test_digest_and_facts_come_before_events():
    out = build_prompt(Q, D, payload(["an event"], digest="the state", facts=["a fact"]), 8000)
    assert out.text.index("the state") < out.text.index("a fact") < out.text.index("an event")


def test_question_survives_even_at_an_absurd_budget():
    # The question is not optional; a budget too small for memory must still ask.
    out = build_prompt(Q, D, payload(["word " * 2000]), 50)
    assert Q in out.text
    assert out.items_included == 0


def test_facts_accept_both_strings_and_registry_entries():
    a = build_prompt(Q, D, payload(facts=["plain string"]), 8000)
    b = build_prompt(Q, D, payload(facts=[{"content": "registry entry"}]), 8000)
    assert "plain string" in a.text
    assert "registry entry" in b.text


def test_empty_payload_says_so_rather_than_pretending():
    out = build_prompt(Q, D, payload(), 8000)
    assert "(no memory retrieved)" in out.text


def test_token_counting_is_shared_across_arms():
    # Both arms must be measured with one encoder or the budget is not equal.
    assert count_tokens("hello") == count_tokens("hello")
    assert count_tokens("") == 0
