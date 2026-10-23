# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }

import genlayer as gl


class Other(gl.contract.Contract):
    data: str

    def __init__(self, data: str):
        self.data = data

    @gl.public.view
    def test(self) -> str:
        return self.data
