def assert_dict_struct(data, structure):
    if isinstance(structure, dict):
        assert_is_instance(data, dict)
        for key, value in structure.items():
            assert key in data
            assert_dict_struct(data[key], value)
    elif isinstance(structure, list):
        assert_is_instance(data, list)
        for item in data:
            assert_dict_struct(item, structure[0])
    else:
        assert_is_instance(data, structure)


def assert_is_instance(data, structure):
    assert isinstance(data, structure), f"Expected {structure}, but got {data}"


def has_error_status(result: dict) -> bool:
    return "error" in result


def has_success_status(result: dict) -> bool:
    return "error" not in result
