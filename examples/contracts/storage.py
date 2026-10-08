# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

import genlayer as gl


# contract class
class Storage(gl.contract.Contract):
    storage: str

    # constructor
    def __init__(self, initial_storage: str):
        self.storage = initial_storage

    # read methods must be annotated with view
    @gl.public.view
    def get_storage(self) -> str:
        return self.storage

    # write method
    @gl.public.write
    def update_storage(self, new_storage: str) -> None:
        self.storage = new_storage
