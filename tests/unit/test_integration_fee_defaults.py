from types import SimpleNamespace

from tests.common.fee_defaults import (
    fees_argument_was_omitted,
    forward_gltest_fee_kwargs,
    gltest_fees_are_unspecified,
    install_gltest_fee_bridge,
)


def test_fee_default_injection_preserves_explicit_gasless_none():
    assert fees_argument_was_omitted({}) is True
    assert fees_argument_was_omitted({"fees": None}) is False
    assert fees_argument_was_omitted({"fees": {"feeValue": 1}}) is False


def test_gltest_default_fee_pair_is_treated_as_omitted():
    assert gltest_fees_are_unspecified(None, None) is True
    assert gltest_fees_are_unspecified({"feeValue": 1}, None) is False
    assert gltest_fees_are_unspecified(None, 1) is False

    def forwarded(_call, fees, fee_value):
        return {"fees": fees, "fee_value": fee_value}

    assert forward_gltest_fee_kwargs(forwarded, object(), None, None) == {}
    assert forward_gltest_fee_kwargs(forwarded, object(), {"feeValue": 1}, None) == {
        "fees": {"feeValue": 1},
        "fee_value": None,
    }
    assert forward_gltest_fee_kwargs(forwarded, object(), None, 1) == {
        "fees": None,
        "fee_value": 1,
    }


def test_gltest_fee_bridge_is_installed_and_idempotent():
    def forwarded(_call, fees, fee_value):
        return {"fees": fees, "fee_value": fee_value}

    contract_factory = SimpleNamespace(_fee_kwargs=forwarded)
    install_gltest_fee_bridge(contract_factory)
    install_gltest_fee_bridge(contract_factory)

    assert contract_factory._fee_kwargs(object(), None, None) == {}
    assert contract_factory._fee_kwargs(object(), {"feeValue": 1}, None) == {
        "fees": {"feeValue": 1},
        "fee_value": None,
    }
