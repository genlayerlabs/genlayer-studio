def fees_argument_was_omitted(call_kwargs: dict) -> bool:
    """Distinguish SDK omission from an intentional gasless ``fees=None``."""
    return "fees" not in call_kwargs


def gltest_fees_are_unspecified(fees, fee_value) -> bool:
    """Recognize gltest's default fee pair before it reaches the SDK.

    ``ContractFactory`` has no omitted-value sentinel and always forwards both
    defaults.  Dropping that pair lets the integration adapter obtain the
    canonical Studio estimate.  Direct SDK calls still preserve an explicit
    ``fees=None`` as an intentional gasless request.
    """
    return fees is None and fee_value is None


def forward_gltest_fee_kwargs(original, call, fees, fee_value) -> dict:
    """Bridge gltest defaults without changing direct SDK call semantics."""
    if gltest_fees_are_unspecified(fees, fee_value):
        return {}
    return original(call, fees, fee_value)


def install_gltest_fee_bridge(contract_factory) -> None:
    """Install the fee bridge on a gltest contract-factory module."""
    current = contract_factory._fee_kwargs
    original = getattr(current, "_studio_original_fee_kwargs", current)

    def fee_aware(call, fees, fee_value):
        return forward_gltest_fee_kwargs(original, call, fees, fee_value)

    fee_aware._studio_original_fee_kwargs = original
    contract_factory._fee_kwargs = fee_aware


def install_gltest_fee_bridges(contract_factory, contract) -> None:
    """Install the bridge on both gltest transaction entry points.

    Deployments and writes have separate private ``_fee_kwargs`` helpers in
    gltest.  Both helpers turn their public ``None`` defaults into an explicit
    SDK ``fees=None`` argument, so both must be adapted for fee-enabled Studio
    integration runs.
    """
    install_gltest_fee_bridge(contract_factory)
    install_gltest_fee_bridge(contract)
