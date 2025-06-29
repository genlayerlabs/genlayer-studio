from genlayer import *


class MultiFileContract(gl.Contract):
    other_addr: Address

    def __init__(self):
        with open("/contract/other.py", "rt") as f:
            text = f.read()
        self.other_addr = gl.deploy_contract(
            code=text.encode("utf-8"), args=["123"], salt_nonce=1
        )

    @gl.public.write
    def wait(self) -> None:
        pass

    @gl.public.view
    def test(self) -> str:
        return gl.ContractAt(self.other_addr).view().test()
