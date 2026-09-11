# v0.3.0
# {
#   "Seq": [
#     { "Depends": "py-lib-genlayer-embeddings:hqpree1t3470fnac2aeee1y5c2205k22bgk1p98sg8m3s1ndmxbg" },
#     { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
#   ]
# }

import numpy as np
import genlayer as gl
from genlayer.types import *
from genlayer.storage import TreeMap
import genlayer_embeddings as gle

from dataclasses import dataclass
import typing


@gl.storage.allow
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
    removed_log_ids: TreeMap[u256, bool]

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
        for result in self.vector_store.knn(emb, len(self.vector_store)):
            log_id = result.value.log_id
            if log_id in self.removed_log_ids and self.removed_log_ids[log_id]:
                continue
            if log_id not in self.log_vector_ids:
                continue
            if self.log_vector_ids[log_id] != result.id:
                continue
            return {
                "vector": list(str(x) for x in result.key),
                "similarity": str(1 - result.distance),
                "id": result.value.log_id,
                "text": result.value.text,
            }
        return None

    @gl.public.write
    def add_log(self, log: str, log_id: int) -> None:
        key = log_id
        if key in self.log_vector_ids:
            # VecDBElement.key (the stored vector used by knn()) is
            # read-only - only .value can be updated in place. Re-embedding
            # requires removing the stale entry and inserting a fresh one,
            # otherwise a changed log stays ranked by its old embedding.
            self.vector_store.get_by_id(self.log_vector_ids[key]).remove()

        emb = self.get_embedding(log)
        vector_id = self.vector_store.insert(emb, StoreValue(text=log, log_id=key))
        self.log_vector_ids[key] = vector_id
        self.removed_log_ids[key] = False

    @gl.public.write
    def update_log(self, log_id: int, log: str) -> None:
        key = log_id
        if key in self.log_vector_ids:
            # See add_log: .key is read-only, must remove+reinsert to
            # actually re-embed rather than leaving the stale vector.
            self.vector_store.get_by_id(self.log_vector_ids[key]).remove()

        emb = self.get_embedding(log)
        vector_id = self.vector_store.insert(emb, StoreValue(text=log, log_id=key))
        self.log_vector_ids[key] = vector_id
        self.removed_log_ids[key] = False

    @gl.public.write
    def remove_log(self, id: int) -> None:
        key = id
        if key in self.log_vector_ids:
            self.removed_log_ids[key] = True
