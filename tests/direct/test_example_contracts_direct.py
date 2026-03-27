"""Direct-mode tests for example contracts.

Validates that example contracts deploy and execute correctly without
needing validators or external services.
"""

from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "contracts"
TEST_CONTRACTS_DIR = (
    Path(__file__).resolve().parents[1] / "integration" / "icontracts" / "contracts"
)


def _addr_hex(addr_bytes: bytes) -> str:
    return "0x" + addr_bytes.hex()


class TestStorage:
    def test_deploy_and_read(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(EXAMPLES_DIR / "storage.py"), "hello")
        assert contract.get_storage() == "hello"

    def test_update_storage(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(EXAMPLES_DIR / "storage.py"), "initial")
        contract.update_storage("updated")
        assert contract.get_storage() == "updated"


class TestUserStorage:
    def test_deploy_and_update(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(EXAMPLES_DIR / "user_storage.py"))
        contract.update_storage("my_data")
        result = contract.get_account_storage(_addr_hex(direct_alice))
        assert result == "my_data"


class TestLlmErc20:
    def test_deploy_and_balances(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(EXAMPLES_DIR / "llm_erc20.py"), 1000)
        balances = contract.get_balances()
        alice_hex = _addr_hex(direct_alice)
        # Balance keys use checksummed hex from Address.as_hex
        assert any(k.lower() == alice_hex.lower() for k in balances)

    def test_transfer(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        direct_vm.sender = direct_alice
        alice_hex = _addr_hex(direct_alice)
        bob_hex = _addr_hex(direct_bob)
        contract = direct_deploy(str(EXAMPLES_DIR / "llm_erc20.py"), 1000)

        # Wrap in ```json fences so .replace("```json","").replace("```","")
        # in contract code strips them and json.loads gets a plain string
        mock_json = (
            '{"transaction_success": true, "transaction_error": "", '
            '"updated_balances": {"' + alice_hex + '": 900, "' + bob_hex + '": 100}}'
        )
        direct_vm.mock_llm(r".*", "```json\n" + mock_json + "\n```")

        contract.transfer(100, bob_hex)
        assert contract.get_balance_of(alice_hex) == 900
        assert contract.get_balance_of(bob_hex) == 100


class TestPayableEscrow:
    def test_deploy(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(TEST_CONTRACTS_DIR / "payable_escrow.py"))
        assert contract.get_deposited() == 0

    def test_deposit_with_value(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(TEST_CONTRACTS_DIR / "payable_escrow.py"))

        direct_vm.value = 500
        contract.deposit()
        direct_vm.value = 0

        assert contract.get_deposited() == 500

    def test_deposit_zero_value_rejected(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(TEST_CONTRACTS_DIR / "payable_escrow.py"))

        direct_vm.value = 0
        with direct_vm.expect_revert("zero value"):
            contract.deposit()

    def test_multiple_deposits_accumulate(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(TEST_CONTRACTS_DIR / "payable_escrow.py"))

        direct_vm.value = 300
        contract.deposit()
        direct_vm.value = 200
        contract.deposit()
        direct_vm.value = 0

        assert contract.get_deposited() == 500

    def test_withdraw_not_depositor(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(TEST_CONTRACTS_DIR / "payable_escrow.py"))

        direct_vm.value = 500
        contract.deposit()
        direct_vm.value = 0

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("not depositor"):
            contract.withdraw(direct_bob)

    def test_withdraw_nothing_as_depositor(
        self, direct_vm, direct_deploy, direct_alice
    ):
        """Depositor is zero address initially — alice can't withdraw because she's not depositor."""
        direct_vm.sender = direct_alice
        contract = direct_deploy(str(TEST_CONTRACTS_DIR / "payable_escrow.py"))

        with direct_vm.expect_revert("not depositor"):
            contract.withdraw(direct_alice)
