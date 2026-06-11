# v0.2.16
# {
#   "Seq": [
#     { "Depends": "py-lib-genlayer-embeddings:0wcvi35grdr47ynkckzriz5sjn5080w2njk7v2cqx3xpp6p1989y" },
#     { "Depends": "py-genlayer:1zr6nqk597d97kg0dyxg0shhrykx5v02zjgnyrajapy4wlqvfvwh" }
#   ]
# }

import numpy as np
import genlayer as gl
from genlayer import *
from genlayer.storage import allow
import genlayer_embeddings as gle

from dataclasses import dataclass
import typing


@allow
@dataclass
class StoreValue:
    log_id: u256
    text: str


# contract class
class LogIndexer(gl.contract.Contract):
    # The 0wcvi35 embeddings runner's VecDB takes an explicit Distance type
    # parameter.
    vector_store: gle.VecDB[
        np.float32, typing.Literal[384], StoreValue, gle.EuclideanDistanceSquared
    ]

    def __init__(self):
        pass

    def get_embedding_generator(self):
        return gle.SentenceTransformer("all-MiniLM-L6-v2")

    def get_embedding(
        self, txt: str
    ) -> np.ndarray[tuple[typing.Literal[384]], np.dtypes.Float32DType]:
        return self.get_embedding_generator()(txt)

    @gl.public.view
    def get_closest_vector(self, text: str) -> dict | None:
        emb = self.get_embedding(text)
        result = list(self.vector_store.knn(emb, 1))
        if len(result) == 0:
            return None
        result = result[0]
        return {
            "vector": list(str(x) for x in result.key),
            "similarity": str(1 - result.distance),
            "id": result.value.log_id,
            "text": result.value.text,
        }

    @gl.public.write
    def add_log(self, log: str, log_id: int) -> None:
        emb = self.get_embedding(log)
        self.vector_store.insert(emb, StoreValue(text=log, log_id=u256(log_id)))

    @gl.public.write
    def update_log(self, log_id: int, log: str) -> None:
        # Locate the element by exact text match via plain iteration instead
        # of knn: the nearest-neighbour search ends in the same text-equality
        # check anyway, and the cover-tree knn currently trips GenVM main's
        # deterministic-mode float trap (wasm_trap DeterministicMode) when
        # invoked from a write method. Views (get_closest_vector) still
        # exercise knn.
        for elem in self.vector_store:
            if elem.value.text == log:
                elem.value.log_id = u256(log_id)

    @gl.public.write
    def remove_log(self, id: int) -> None:
        for el in self.vector_store:
            if el.value.log_id == id:
                el.remove()
