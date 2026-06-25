# v0.3-dev
# {
#   "Seq": [
#     { "Depends": "py-lib-genlayer-embeddings:1md4i1njqn0h0psgjdl97mz10rpp1268ychpn6l2dmr81fbvxknb" },
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
    # The v0.3 embeddings runner's VecDB takes an explicit metric type.
    vector_store: gle.VecDB[
        np.float32, typing.Literal[384], StoreValue, gle.EuclideanDistance
    ]
    log_vector_ids: TreeMap[u256, u32]

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
        key = u256(log_id)
        if key in self.log_vector_ids:
            self.vector_store.get_by_id(self.log_vector_ids[key]).remove()

        emb = self.get_embedding(log)
        vector_id = self.vector_store.insert(emb, StoreValue(text=log, log_id=key))
        self.log_vector_ids[key] = u32(vector_id)

    @gl.public.write
    def update_log(self, log_id: int, log: str) -> None:
        key = u256(log_id)
        if key in self.log_vector_ids:
            self.vector_store.get_by_id(self.log_vector_ids[key]).remove()

        emb = self.get_embedding(log)
        vector_id = self.vector_store.insert(emb, StoreValue(text=log, log_id=key))
        self.log_vector_ids[key] = u32(vector_id)

    @gl.public.write
    def remove_log(self, id: int) -> None:
        key = u256(id)
        if key in self.log_vector_ids:
            self.vector_store.get_by_id(self.log_vector_ids[key]).remove()
            del self.log_vector_ids[key]
