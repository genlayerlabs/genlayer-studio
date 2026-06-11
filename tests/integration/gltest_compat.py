import ast
import io
import zipfile
from pathlib import Path
from typing import Optional, Union

import gltest.artifacts
import gltest.artifacts.contract as contract_artifacts
import gltest.contracts.contract_factory as contract_factory
from gltest.artifacts.contract import ContractDefinition
from gltest_cli.config.general import get_general_config


def _is_genlayer_contract_base(base: ast.expr) -> bool:
    if not isinstance(base, ast.Attribute):
        return False
    if isinstance(base.value, ast.Name):
        return base.value.id == "gl" and base.attr == "Contract"
    if isinstance(base.value, ast.Attribute):
        return (
            isinstance(base.value.value, ast.Name)
            and base.value.value.id == "gl"
            and base.value.attr == "contract"
            and base.attr == "Contract"
        )
    return False


def search_path_by_class_name(contracts_dir: Path, contract_name: str) -> Path:
    """Search for a file by class name in the contracts directory."""
    matching_files = []
    exclude_dirs = {".venv", "venv", "env", "build", "dist", "__pycache__", ".git"}

    for file_path in contracts_dir.rglob("*"):
        if any(exclude_dir in file_path.parts for exclude_dir in exclude_dirs):
            continue
        if file_path.suffix not in [".gpy", ".py"]:
            continue
        try:
            with open(file_path, "r") as f:
                content = f.read()
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == contract_name:
                    for base in node.bases:
                        if _is_genlayer_contract_base(base):
                            matching_files.append(file_path)
                            break
                    break
        except Exception as e:
            raise ValueError(f"Error reading file {file_path}: {e}") from e

    if len(matching_files) == 0:
        raise FileNotFoundError(
            f"Contract {contract_name} not found at: {contracts_dir}"
        )
    if len(matching_files) > 1:
        file_paths_str = ", ".join(str(f) for f in matching_files)
        raise ValueError(
            f"Multiple contracts named '{contract_name}' found in contracts directory. "
            f"Found in files: {file_paths_str}. Please ensure contract names are unique."
        ) from None

    return matching_files[0]


def _extract_contract_name_from_file(file_path: Path) -> str:
    """Extract contract name from a file by parsing the AST."""
    try:
        with open(file_path, "r") as f:
            content = f.read()
        tree = ast.parse(content)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if _is_genlayer_contract_base(base):
                        return node.name
    except Exception as e:
        raise ValueError(f"Error parsing contract file {file_path}: {e}") from e

    raise ValueError(f"No valid contract class found in {file_path}")


def _compute_contract_code(
    main_file_path: Path,
    runner_file_path: Optional[Path] = None,
) -> str:
    if runner_file_path is None:
        return main_file_path.read_text()

    main_file_dir = main_file_path.parent
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, mode="w") as zip_file:
        zip_file.write(main_file_path, "contract/__init__.py")
        for file_path in main_file_dir.rglob("*"):
            if file_path.name in ["runner.json", "__init__.py"]:
                continue
            rel_path = file_path.relative_to(main_file_dir)
            zip_file.write(file_path, f"contract/{rel_path}")
        zip_file.write(runner_file_path, "runner.json")
    buffer.flush()
    return buffer.getvalue()


def _create_contract_definition(
    main_file_path: Path, contract_name: str
) -> ContractDefinition:
    main_file_dir = main_file_path.parent
    runner_file_path = None
    if main_file_path.name in ["__init__.py", "__init__.gpy"]:
        runner_file_path = main_file_dir.joinpath("runner.json")
        if not runner_file_path.exists():
            runner_file_path = None

    contract_code = _compute_contract_code(main_file_path, runner_file_path)

    return ContractDefinition(
        contract_name=contract_name,
        contract_code=contract_code,
        main_file_path=main_file_path,
        runner_file_path=runner_file_path,
    )


def find_contract_definition_from_name(
    contract_name: str,
) -> Optional[ContractDefinition]:
    general_config = get_general_config()
    contracts_dir = general_config.get_contracts_dir()
    if not contracts_dir.exists():
        raise FileNotFoundError(f"Contracts directory not found at: {contracts_dir}")

    main_file_path = search_path_by_class_name(contracts_dir, contract_name)
    return _create_contract_definition(main_file_path, contract_name)


def find_contract_definition_from_path(
    contract_file_path: Union[str, Path],
) -> ContractDefinition:
    general_config = get_general_config()
    contracts_dir = general_config.get_contracts_dir()
    if not contracts_dir.exists():
        raise FileNotFoundError(f"Contracts directory not found at: {contracts_dir}")

    main_file_path = contracts_dir / contract_file_path
    if not main_file_path.exists():
        raise FileNotFoundError(f"Contract file not found at: {main_file_path}")

    contract_name = _extract_contract_name_from_file(main_file_path)

    return _create_contract_definition(main_file_path, contract_name)


def apply() -> None:
    contract_artifacts.search_path_by_class_name = search_path_by_class_name
    contract_artifacts.find_contract_definition_from_name = (
        find_contract_definition_from_name
    )
    contract_artifacts.find_contract_definition_from_path = (
        find_contract_definition_from_path
    )

    gltest.artifacts.find_contract_definition_from_name = (
        find_contract_definition_from_name
    )
    gltest.artifacts.find_contract_definition_from_path = (
        find_contract_definition_from_path
    )

    contract_factory.find_contract_definition_from_name = (
        find_contract_definition_from_name
    )
    contract_factory.find_contract_definition_from_path = (
        find_contract_definition_from_path
    )
