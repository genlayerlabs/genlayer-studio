def fees_argument_was_omitted(call_kwargs: dict) -> bool:
    """Distinguish SDK omission from an intentional gasless ``fees=None``."""
    return "fees" not in call_kwargs
