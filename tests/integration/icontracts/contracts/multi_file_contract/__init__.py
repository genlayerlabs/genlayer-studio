# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


class MultiFileContract(gl.Contract):
    other_addr: Address

    def __init__(self):
        with open("/contract/other.py", "rt") as f:
            text = f.read()
        self.other_addr = gl.deploy_contract(
            code=text.encode("utf-8"),
            args=["123"],
            salt_nonce=u256(1),
            value=u256(0),
        )

    @gl.public.write
    def wait(self) -> None:
        pass

    @gl.public.view
    def test(self) -> str:
        return gl.get_contract_at(self.other_addr).view().test()
