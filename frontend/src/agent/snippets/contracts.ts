export function storageContractSnippet(name = 'Storage') {
  return `from genlayer import *
class ${name}(gl.Contract):
    value: u256

    def __init__(self, initial: u256):
        self.value = initial

    @gl.public.view
    def get(self) -> u256:
        return self.value

    @gl.public.write
    def set(self, v: u256) -> None:
        self.value = v
`;
}

export function registryContractSnippet(name = 'Registry') {
  return `from genlayer import *
class ${name}(gl.Contract):
    items: DynArray[str]

    def __init__(self):
        self.items = DynArray[str]()

    @gl.public.view
    def list(self) -> DynArray[str]:
        return self.items

    @gl.public.write
    def add(self, item: str) -> None:
        self.items.append(item)
`;
}


