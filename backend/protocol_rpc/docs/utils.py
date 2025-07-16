"""
Utility functions for the documentation system.
"""


def format_category_class(category: str) -> str:
    """Convert category name to CSS class name"""
    return category.lower().replace(" ", "").replace("-", "").replace("_", "")


def generate_method_examples(method_name: str, parameters: list) -> list:
    """Generate example requests for a method"""
    examples = []

    # Basic example with required parameters
    request_params = []
    for param in parameters:
        if param.get("required", True):
            param_type = param.get("type", "str")
            param_name = param.get("name", "")

            if param_type == "str":
                if "address" in param_name:
                    request_params.append("0x742d35Cc6634C0532925a3b844Bc9e7595f6E123")
                elif "hash" in param_name:
                    request_params.append("0x123456789abcdef...")
                else:
                    request_params.append(f"example_{param_name}")
            elif param_type == "int":
                request_params.append(1000000000000000000)  # 1 ETH in wei
            elif param_type == "bool":
                request_params.append(True)
            elif param_type.startswith("List"):
                request_params.append([])
            elif param_type.startswith("Dict"):
                request_params.append({})
            else:
                request_params.append(None)

    example = {
        "request": {
            "jsonrpc": "2.0",
            "method": method_name,
            "params": request_params,
            "id": 1,
        },
        "response": {"jsonrpc": "2.0", "result": "0x...", "id": 1},  # Placeholder
    }

    examples.append(example)
    return examples


def get_method_category_styles() -> dict:
    """Get CSS styles for different method categories"""
    return {
        "simulator": {"background": "#0ea5e9", "color": "#ffffff"},  # Accent blue
        "genlayer": {"background": "#22c55e", "color": "#ffffff"},  # Success green
        "ethereumcompatible": {
            "background": "#627eea",
            "color": "#ffffff",
        },  # Ethereum blue
        "network": {"background": "#f59e0b", "color": "#ffffff"},  # Warning orange
        "utility": {"background": "#6b7280", "color": "#ffffff"},  # Gray
    }
