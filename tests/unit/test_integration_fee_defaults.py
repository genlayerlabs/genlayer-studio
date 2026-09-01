from tests.integration.conftest import _fees_argument_was_omitted


def test_fee_default_injection_preserves_explicit_gasless_none():
    assert _fees_argument_was_omitted({}) is True
    assert _fees_argument_was_omitted({"fees": None}) is False
    assert _fees_argument_was_omitted({"fees": {"feeValue": 1}}) is False
