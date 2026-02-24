def _assert_dict_struct(data, structure):
    if isinstance(structure, dict):
        assert_is_instance(data, dict)
        for key, value in structure.items():
            try:
                assert key in data
                assert_dict_struct(data[key], value)
            except BaseException as e:
                e.add_note(f"dict key {key!r}")
                raise
    elif isinstance(structure, list):
        assert_is_instance(data, list)
        for idx, item in enumerate(data):
            try:
                assert_dict_struct(item, structure[0])
            except BaseException as e:
                e.add_note(f"list item [{idx}]")
                raise
    else:
        assert_is_instance(data, structure)


def assert_dict_struct(data, structure):
    try:
        return _assert_dict_struct(data, structure)
    except BaseException as e:
        e.add_note(f"while asserting dict structure of {data!r}")


def assert_is_instance(data, structure):
    assert isinstance(data, structure), f"Expected {structure}, but got {data}"


def has_error_status(result: dict) -> bool:
    return "error" in result


def has_success_status(result: dict) -> bool:
    return "error" not in result
