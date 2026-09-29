# Compliance control map

How Internal Brain's controls line up with the rules a Singapore financial institution already
works under. Each row names the control, where it lives in the code, and the test or evidence that
proves it. This is an engineering map for reviewers, not legal advice: each institution maps its
own obligations.

## MAS Technology Risk Management Guidelines (January 2021)

| TRM section | What the Brain does | Code | Evidence |
|---|---|---|---|
| 9.1 User access management: access on need-to-have, revoked promptly | Every answer is computed from the asker's live entitlements at each source platform. Gate 1 filters inside the index query; Gate 2 re-checks each document at the source before the model sees it. Revocation takes effect on the next question, with or without a webhook | `core/entitlements.py`, `core/index.py`, `core/verify_live.py` | `tests/test_permission_changes.py`, `tests/test_scenarios.py` (scenario 4), `docs/scenarios/scenario-4-live-revocation.md` |
| 9.1 Least privilege for the system itself | The connector credential is read-only; query-time checks run as the asker. The model holds no credential and no tool | `adapters/`, `core/llm.py` | `docs/architecture.md` (trust boundary) |
| 9.2 Privileged access management | Admin and compliance roles are separate; every compliance read of the log is itself logged | `api/deps.py`, `api/routes_audit.py` | `tests/test_audit_chain.py` |
| 10.2 Cryptographic key management | Audit checkpoints are signed with Ed25519; the private key lives outside the database (`AUDIT_KEY_DIR`), so whoever can write the database cannot re-sign it | `audit/log.py` | `tests/test_audit_chain.py`, `scripts/tamper_demo.py` |
| 11.1 Data security: data protected at rest and in use | Card numbers, NRICs, bank accounts and secrets are masked at ingestion, so the index, the prompt and the answer never hold full values; output is masked again | `core/dlp.py`, `core/sync.py`, `core/pipeline.py` | `tests/test_guardrails.py` |
| 11.1 Data leakage | A restricted document and a non-existent one produce byte-identical responses in the same time; 350 adversarial queries: zero leaks | `core/pipeline.py`, `evals/leak.py` | `docs/evidence/leak-eval.md`, `docs/evidence/side-channels.md` |
| 12.2 Cyber event monitoring and detection | Deterministic insider-threat rules over the audit trail: bursts of blocked questions, probing one restricted space, retries after revocation, guessing document ids, the model citing what it was not given. Alerts are chained entries streamed live to the console | `audit/alerts.py`, `api/routes_audit.py` | `tests/test_guardrails.py`, `tests/test_demo_ops.py` |
| 12.3 Incident response and forensics | For any question: who asked, their tokens at that moment, every document considered and why it was allowed or denied, the exact chunks the model saw (with hashes), the model's raw output and what the guard removed | `audit/log.py`, `audit/queries.py` | `docs/scenarios/scenario-5-audit-inquiry.md` |
| 15 IT audit: tamper-evident records | SHA-256 hash chain over every entry plus signed checkpoints; `python -m internal_brain.audit.verify` finds an edited row or a deleted one | `audit/verify.py` | `tests/test_audit_chain.py` |

## PDPA and the PDPC's NRIC guidance

| Requirement | What the Brain does | Evidence |
|---|---|---|
| Protection obligation (PDPA s24): reasonable security arrangements | Permission-correct retrieval, DLP masking at ingestion, uniform denials, append-only audit | as above |
| PDPC Advisory Guidelines on NRIC numbers: display at most a partial NRIC (last three digits and the checksum letter) | NRICs with a valid checksum are masked to `•••••567D` at ingestion and in answers | `core/dlp.py`, `tests/test_guardrails.py` |
| PDPC and CSA advisory (2025): NRIC numbers are not to be used as passwords or for authentication | The Brain never authenticates with NRICs: identity comes from the IdP session; NRICs in content are treated as sensitive data | `core/identity.py` |
| Data breach notification (Part 6A): know what was exposed, to whom, when | The audit trail answers "who saw which document" per question, and data-at-rest alerts flag personal data pasted into chat tools | `audit/queries.py`, `audit/alerts.py` |

## PCI DSS v4.0.1 (for card data that ends up in collaboration tools)

| Requirement | What the Brain does | Evidence |
|---|---|---|
| 3.4.1 PAN is masked when displayed (at most the BIN and last four digits) | Answers and source views show `••••1111` | `tests/test_guardrails.py` |
| 3.5.1 PAN is rendered unreadable wherever it is stored | Truncated at ingestion (last four kept, Luhn and issuer prefix checked), so the index never stores a full PAN | `core/dlp.py` |
| 10.2 and 10.3 Audit logs capture access and are protected from modification | Every access decision is logged before the answer returns; the chain and signed checkpoints make modification detectable | `audit/log.py`, `tests/test_audit_chain.py` |

Masking in the Brain does not remove the value from the source platform; the sensitive-data alert
exists so the owner can delete it there.

## IMDA Model AI Governance Framework for Generative AI (2024): the nine dimensions

| Dimension | How it shows up |
|---|---|
| Accountability | Deterministic code, not the model, decides access; every decision has an owner-readable audit entry |
| Data | Only permitted, masked text reaches the model; the index stores ACLs as data and is rebuilt when the pipeline changes |
| Trusted development and deployment | Model-agnostic OpenAI-compatible client (Hunyuan, TokenHub); a stub runs everything offline; CI runs the evals |
| Incident reporting | Alerts and the audit trail give the facts an incident report needs |
| Testing and assurance | Leak eval (350 adversarial queries), golden Q&A quality eval, randomized permission tests at 100k chunks, scenario tests |
| Security | Prompt-injection resistance by construction: document text is data, citations outside the context are stripped, the model has no tools |
| Content provenance | Every sentence carries a citation and the evidence quote from the cited chunk; the drawer opens the exact passage, re-checked live |
| Safety and alignment R&D | NO_ANSWER instead of a guess; a verifier removes sentences their citation does not support |
| AI for public good | Out of scope for an internal tool |

## MAS proposed Guidelines on AI Risk Management (consultation paper, November 2025)

The guidelines were still a proposal when this was written. The areas they cover map onto the
Brain as follows: **governance and oversight** (the audit trail and alerts give the second and third
lines of defence something to review), **AI inventory and risk materiality** (one use case, retrieval
with a model in the loop, with the model outside the trust boundary), **lifecycle controls**: data
management (DLP, ACL-as-data), transparency (per-sentence citations and evidence), human oversight
(compliance console and live stream), testing (the evals above, runnable by anyone with
`make test`), cybersecurity (two gates, uniform denials, signed audit), and third-party AI
(the model provider sees only permitted, masked text; `enable_enhancement` is off for Hunyuan so
no web results enter a grounded answer).

Sources: [MAS TRM Guidelines (2021)](https://www.mas.gov.sg/regulation/guidelines/technology-risk-management-guidelines),
[PDPC Advisory Guidelines on NRIC numbers](https://www.pdpc.gov.sg/guidelines-and-consultation/2020/02/advisory-guidelines-on-the-personal-data-protection-act-for-nric-and-other-national-identification-numbers),
[PCI DSS v4.0.1](https://www.pcisecuritystandards.org/document_library/),
[IMDA Model AI Governance Framework for Generative AI](https://aiverifyfoundation.sg/resources/mgf-gen-ai/),
[MAS consultation paper on AI risk management (Nov 2025)](https://www.mas.gov.sg/publications/consultations/2025/consultation-paper-on-guidelines-on-artificial-intelligence-risk-management).
