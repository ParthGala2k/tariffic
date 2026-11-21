"""Approved rules are executable: the explicit-clause credit rule computes the credit itself."""
from __future__ import annotations

import pytest

from desk import policies

RULE = {"kind": "explicit_clause_credit", "credit_basis": "full tariff surcharge billed on machines whose parts came from refunded entries",
        "debit_account": "Income:Sales:TariffSurcharge", "credit_account": "Liabilities:CustomerCredits"}


def test_explicit_clause_credit_computes_bean_street(conn, cfg):
    out = policies._exec_explicit_clause_credit(conn, RULE, {"customer_id": "CUST-001", "refund_id": "RFD-001"}, cfg)
    assert out["credit_total"] == "1632.00"
    assert {(c["invoice_id"], c["credit"]) for c in out["by_invoice"]} == {("INV-1001", "1152.00"), ("INV-1002", "480.00")}
    assert out["debit_account"] == "Income:Sales:TariffSurcharge" and out["raw_refs"]


def test_explicit_clause_credit_refuses_an_ambiguous_clause(conn, cfg):
    with pytest.raises(policies.PolicyError, match="ambiguous"):
        policies._exec_explicit_clause_credit(conn, RULE, {"customer_id": "CUST-002", "refund_id": "RFD-001"}, cfg)
    with pytest.raises(policies.PolicyError, match="needs inputs"):
        policies._exec_explicit_clause_credit(conn, RULE, {}, cfg)
