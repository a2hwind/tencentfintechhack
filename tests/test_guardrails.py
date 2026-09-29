"""Fintech guardrails: sensitive values never reach the index, the prompt or the answer in full,
and the audit trail raises deterministic insider-threat and data-hygiene alerts."""

from __future__ import annotations

from conftest import SCENARIO_1

from internal_brain.core.dlp import luhn_valid, mask, nric_checksum_valid

PAN = "4111 1111 1111 1111"
NRIC = "S1234567D"
PAYMENTS_Q = "What card was charged twice in the payments channel and what was the refund account?"
PASSWORD_Q = "What is the staging replica password?"


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------
def test_detectors_mask_and_keep_only_what_is_allowed():
    r = mask(f"card {PAN}, amex 3782-822463-10005, NRIC {NRIC}, bank account no. 123-456789-001, password is Tr0ub4dor&3.")
    assert r.as_dict() == {"bank_account": 1, "card": 2, "nric": 1, "secret": 1}
    assert "1111 1111" not in r.text and "••••1111" in r.text  # PCI DSS: truncation, last four only
    assert "••••0005" in r.text
    assert "•••••567D" in r.text and NRIC not in r.text  # PDPC: at most the last 3 digits and checksum
    assert "••••9001" in r.text
    assert "password is [secret]." in r.text and "Tr0ub4dor" not in r.text


def test_detectors_leave_ordinary_engineering_text_alone():
    text = (
        "Slack ts 1789647286.000200, epoch ms 1790144181583, date 2026-09-22, phone +65 6123 4567, "
        "PAY-231 and DBM-42, 60% of partitions, version 7 to 8, invalid card 4111111111111112, "
        "not an NRIC S1234567A, the password policy requires rotation."
    )
    r = mask(text)
    assert not r.found and r.text == text


def test_masking_is_idempotent_and_validators_are_correct():
    once = mask(f"{PAN} {NRIC} xoxb-123456789012-abcdefABCDEF AKIAIOSFODNN7EXAMPLE")
    twice = mask(once.text)
    assert twice.text == once.text and not twice.found
    assert luhn_valid("4111111111111111") and not luhn_valid("4111111111111112")
    assert nric_checksum_valid("S", "1234567", "D") and not nric_checksum_valid("S", "1234567", "A")


# ---------------------------------------------------------------------------
# Ingestion, prompt and answer
# ---------------------------------------------------------------------------
def test_index_and_prompt_never_hold_full_values(brain):
    index = brain.brain.index
    with index.lock:
        rows = index.conn.execute("SELECT text FROM chunks").fetchall()
    corpus = "\n".join(r["text"] for r in rows)
    assert "4111 1111 1111 1111" not in corpus and "4111111111111111" not in corpus
    assert NRIC not in corpus and "Tr0ub4dor" not in corpus and "456789" not in corpus
    assert "••••1111" in corpus and "•••••567D" in corpus
    thread = next(i for i in index.list_items() if i.dlp.get("card"))
    assert thread.dlp == {"bank_account": 1, "card": 1, "nric": 1}


def test_answer_shows_masked_values_only(brain):
    body, seq, _ = brain.ask("jdoe", PAYMENTS_Q)
    assert not body["no_result"]
    assert "••••1111" in body["answer"] and "4111" not in body["answer"]
    assert "•••••567D" in body["answer"] and NRIC not in body["answer"]
    for sentence in body["sentences"]:
        for ev in sentence["evidence"]:
            assert "4111 1111" not in ev["quote"]
    body, _, _ = brain.ask("jdoe", PASSWORD_Q)
    assert "Tr0ub4dor" not in body["answer"]
    assert "[secret]" in body["answer"]


def test_model_output_is_masked_even_if_the_model_invents_a_value():
    from internal_brain.core.assemble import Context, ContextDoc
    from internal_brain.core.guard import guard
    from internal_brain.core.pipeline import mask_output
    from internal_brain.core.retrieve import ChunkHit

    ctx = Context()
    ctx.docs.append(ContextDoc("slack:C0PAY:1", "slack", "t", "2026-09-20T00:00:00Z", None, None, "x", 1, [ChunkHit("slack:C0PAY:1#0", "text", "sha", 1.0)]))
    result = guard("The card on file is 5555 5555 5555 4444 [doc:slack:C0PAY:1].", ctx)
    counts = mask_output(result)
    assert counts == {"card": 1}
    assert "5555 5555" not in result.text and "••••4444" in result.text


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
def _alerts(brain) -> list[dict]:
    return brain.compliance("/audit/alerts")["alerts"]


def test_sensitive_data_at_rest_alerts_on_ingestion(brain):
    alerts = [a for a in _alerts(brain) if a["rule"] == "sensitive_data_at_rest"]
    subjects = {a["subject"]["id"]: a for a in alerts}
    card_alert = next(a for a in alerts if a.get("counts", {}).get("card"))
    assert card_alert["severity"] == "high" and "#payments" in card_alert["title"]
    assert "4111" not in str(card_alert) and NRIC not in str(card_alert), "alerts never carry the values"
    assert any(a.get("counts", {}).get("secret") for a in subjects.values())
    # one alert per document version, not one per sync
    brain.admin("POST", "/admin/sync/run")
    assert len([a for a in _alerts(brain) if a["rule"] == "sensitive_data_at_rest"]) == len(alerts)


def test_probing_restricted_content_raises_one_grouped_alert(brain):
    brain.ask("ctr-lee", "Where is the Q3 breach report?")
    assert not [a for a in _alerts(brain) if a["rule"] == "container_probe"], "one blocked question is not a pattern"
    brain.ask("ctr-lee", "Summarise the Q3 breach incident report.")
    probes = [a for a in _alerts(brain) if a["rule"] == "container_probe"]
    assert len(probes) == 1 and probes[0]["severity"] == "high"
    assert probes[0]["subject"] == {"type": "user", "id": "ctr-lee"}
    assert "confluence:space:SEC" in probes[0]["containers"]
    assert "Where is the Q3 breach report?" in probes[0]["detail"]
    # the asker saw nothing of this
    body, _, _ = brain.ask("ctr-lee", "Where is the Q3 breach report?")
    assert body["no_result"] and "alert" not in body["answer"].lower()


def test_blocked_burst_and_ordinary_use_stays_quiet(brain):
    for q in (SCENARIO_1, "What's the latest runbook for the payment-service incident?", "Summarise the auth service design discussion and link the decision doc"):
        brain.ask("jdoe", q)
    assert not [a for a in _alerts(brain) if a["subject"].get("id") == "jdoe"], "answered questions with incidental denials raise nothing"
    # three blocked questions on three different topics: a burst, but no single container is probed twice
    for q in (
        "Where is the Q3 breach report?",
        "What did the leads say about the migration cutover in the leads channel?",
        "What does PAY-260 say about PAN fragments in debug logs?",
    ):
        body, _, _ = brain.ask("ctr-lee", q)
        assert body["no_result"]
    rules = {a["rule"] for a in _alerts(brain) if a["subject"].get("id") == "ctr-lee"}
    assert "blocked_burst" in rules and "container_probe" not in rules


def test_retry_after_revocation_alert(brain):
    brain.ask("jdoe", SCENARIO_1)
    brain.admin("POST", "/admin/slack/channels/C0DBM/members", json={"user_id": "jdoe", "member": False, "notify": True})
    brain.ask("jdoe", SCENARIO_1)
    assert not [a for a in _alerts(brain) if a["rule"] == "retry_after_revocation"]
    brain.ask("jdoe", SCENARIO_1)
    retries = [a for a in _alerts(brain) if a["rule"] == "retry_after_revocation"]
    assert retries and retries[0]["doc"].startswith("slack:C0DBM:") and retries[0]["severity"] == "low"


def test_alerts_are_chained_entries_and_gated(brain, client):
    brain.ask("ctr-lee", "Where is the Q3 breach report?")
    brain.ask("ctr-lee", "Summarise the Q3 breach incident report.")
    assert client.get("/audit/alerts", headers={"X-User-Id": "jdoe"}).status_code == 403
    alert = next(a for a in _alerts(brain) if a["rule"] == "container_probe")
    entry = brain.entry(alert["seq"])
    assert entry["kind"] == "alert" and entry["actor"]["id"] == "alert-engine" and entry["entry_hash"] == alert["entry_hash"]
    assert brain.brain.audit.verify().ok
