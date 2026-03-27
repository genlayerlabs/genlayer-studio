# v0.2.4
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


class Storage(gl.Contract):
    storage: str

    def __init__(self, initial_storage: str):
        self.storage = initial_storage

    @gl.public.view
    def get_storage(self) -> str:
        return self.storage

    @gl.public.write
    def update_storage(self, new_storage: str) -> None:
        self.storage = new_storage
