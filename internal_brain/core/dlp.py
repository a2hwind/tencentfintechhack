"""Sensitive-data guard (DLP): card numbers, national IDs, bank accounts and secrets never
reach the index, the prompt or the answer in full.

Masking runs twice. At ingestion, before chunking, so the index (and therefore every prompt
handed to the model in Zone 3) only ever holds truncated values: for card numbers that is PCI
DSS "truncation" (last four digits kept). On the finished answer, as defence in depth against a
model that reconstructs or invents a value. Findings are reported by type and count only; the
values themselves are never logged, stored or displayed.

Detectors are deterministic and conservative:
  card          13-19 digits (spaces or dashes allowed), issuer prefix 2-6, Luhn-valid -> ••••1111
  nric          Singapore NRIC/FIN [STFGM] + 7 digits + letter -> •••••567D (PDPC: last 3 digits
                and checksum at most)
  bank_account  digits following "account" / "acct" / "a/c" -> ••••4321
  secret        private keys, AWS keys, Slack tokens, Stripe keys, JWTs, and values assigned to
                password / secret / api key / token -> [secret]
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

MASK = "•"  # •

PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
TOKEN_RES = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}\b"),  # Slack tokens
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{16,}\b"),  # Stripe-style keys
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWT
    re.compile(r"\bghp_[0-9A-Za-z]{30,}\b"),  # GitHub personal access token
]
ASSIGNED_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|passphrase|secret|api[ _-]?key|access[ _-]?token|auth[ _-]?token)\b"
    r"(\s*(?:is|was|=|:|->)\s*)"
    r"([\"'`]?)([^\s\"'`,;]{4,})\3"
)
CARD_RE = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
NRIC_RE = re.compile(r"\b([STFGM])(\d{7})([A-Z])\b")
BANK_RE = re.compile(r"(?i)\b(?:bank\s+)?(?:account|acct|a/c)(?:\s*(?:no\.?|number|#))?\s*[:#]?\s*((?:\d[ -]?){6,16}\d)\b")

NRIC_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
NRIC_ST_LETTERS = "JZIHGFEDCBA"
NRIC_FG_LETTERS = "XWUTRQPNMLK"


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def nric_checksum_valid(prefix: str, digits: str, letter: str) -> bool:
    """S/T (citizens, PRs) and F/G (FIN) checksums; M-series is accepted on pattern alone."""
    prefix, letter = prefix.upper(), letter.upper()
    if prefix == "M":
        return True
    total = sum(int(d) * w for d, w in zip(digits, NRIC_WEIGHTS))
    if prefix in ("T", "G"):
        total += 4
    table = NRIC_ST_LETTERS if prefix in ("S", "T") else NRIC_FG_LETTERS
    return table[total % 11] == letter


@dataclass
class DlpResult:
    text: str
    counts: Counter = field(default_factory=Counter)

    @property
    def found(self) -> bool:
        return bool(self.counts)

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))


def _mask_card(match: re.Match, counts: Counter) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if not (13 <= len(digits) <= 19) or digits[0] not in "23456" or not luhn_valid(digits):
        return raw
    counts["card"] += 1
    return f"{MASK * 4}{digits[-4:]}"


def _mask_nric(match: re.Match, counts: Counter) -> str:
    prefix, digits, letter = match.group(1), match.group(2), match.group(3)
    if not nric_checksum_valid(prefix, digits, letter):
        return match.group(0)
    counts["nric"] += 1
    return f"{MASK * 5}{digits[-3:]}{letter}"


def _mask_bank(match: re.Match, counts: Counter) -> str:
    whole, number = match.group(0), match.group(1)
    digits = re.sub(r"\D", "", number)
    if len(digits) < 7:
        return whole
    counts["bank_account"] += 1
    return whole[: match.start(1) - match.start(0)] + f"{MASK * 4}{digits[-4:]}"


def mask(text: str) -> DlpResult:
    """Mask every sensitive value in `text`. Idempotent: masked output contains no detectable values."""
    if not text:
        return DlpResult(text or "")
    counts: Counter = Counter()

    def secret(_: re.Match) -> str:
        counts["secret"] += 1
        return "[secret]"

    out = PRIVATE_KEY_RE.sub(lambda m: (counts.update(["secret"]), "[private key]")[1], text)
    for pattern in TOKEN_RES:
        out = pattern.sub(secret, out)

    def assigned(m: re.Match) -> str:
        value = m.group(4)
        trailing = ""
        while value and value[-1] in ".!?)]}":
            trailing = value[-1] + trailing
            value = value[:-1]
        if len(value) < 4 or value.lower() in ("[secret]", "required", "reset", "rotated", "manager", "policy", "expired", "changed"):
            return m.group(0)
        counts["secret"] += 1
        return f"{m.group(1)}{m.group(2)}[secret]{trailing}"

    out = ASSIGNED_SECRET_RE.sub(assigned, out)
    out = BANK_RE.sub(lambda m: _mask_bank(m, counts), out)
    out = CARD_RE.sub(lambda m: _mask_card(m, counts), out)
    out = NRIC_RE.sub(lambda m: _mask_nric(m, counts), out)
    return DlpResult(out, counts)


def severity(counts: dict[str, int]) -> str:
    if counts.get("card") or counts.get("secret"):
        return "high"
    if counts.get("nric") or counts.get("bank_account"):
        return "medium"
    return "low"


KIND_LABELS = {
    "card": "payment card number",
    "nric": "NRIC/FIN number",
    "bank_account": "bank account number",
    "secret": "credential or secret",
}
