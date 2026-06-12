# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# BACKWARD-COMPAT CANARY - DO NOT UPGRADE TO THE MAIN-SDK IDIOM.
# This contract is deliberately kept on the v0.3.0-rc3 runner (old SDK:
# star-exported `gl`, gl.Contract base) to verify that contracts deployed
# against older runners keep executing on the genvm-main executor.
# (The blank line above is load-bearing: the executor parses the leading
# comment block as version + runner JSON, so the note must sit outside it.)

from genlayer import *


class UserStorage(gl.Contract):
    storage: TreeMap[Address, str]

    # constructor
    def __init__(self):
        pass

    # read methods must be annotated
    @gl.public.view
    def get_complete_storage(self) -> dict[str, str]:
        return {k.as_hex: v for k, v in self.storage.items()}

    @gl.public.view
    def get_account_storage(self, account_address: str) -> str:
        return self.storage[Address(account_address)]

    @gl.public.write
    def update_storage(self, new_storage: str) -> None:
        self.storage[gl.message.sender_address] = new_storage
