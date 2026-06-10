from ...types import Address

"""
This module is responsible for working with genvm calldata

Calldata natively supports following types:

#. Primitive types:

	#. python built-in: :py:class:`bool`, :py:obj:`None`, :py:class:`int`, :py:class:`str`, :py:class:`bytes`
	#. :py:meth:`~genlayer.py.types.Address` type

#. Composite types:

	#. :py:class:`list` (and any other :py:class:`collections.abc.Sequence`)
	#. :py:class:`dict` with :py:class:`str` keys (and any other :py:class:`collections.abc.Mapping` with :py:class:`str` keys)

For full calldata specification see `genvm repo <https://github.com/yeagerai/genvm/blob/main/doc/calldata.md>`_
"""

__all__ = (
    "encode",
    "decode",
    "to_str",
    "Encodable",
    "Encodable",
    "EncodableWithDefault",
    "Decoded",
    "CalldataEncodable",
    "DecodingError",
)

import typing
import collections.abc
import dataclasses
import abc
import json

BITS_IN_TYPE = 3

TYPE_SPECIAL = 0
TYPE_PINT = 1
TYPE_NINT = 2
TYPE_BYTES = 3
TYPE_STR = 4
TYPE_ARR = 5
TYPE_MAP = 6

SPECIAL_NULL = (0 << BITS_IN_TYPE) | TYPE_SPECIAL
SPECIAL_FALSE = (1 << BITS_IN_TYPE) | TYPE_SPECIAL
SPECIAL_TRUE = (2 << BITS_IN_TYPE) | TYPE_SPECIAL
SPECIAL_ADDR = (3 << BITS_IN_TYPE) | TYPE_SPECIAL


class CalldataEncodable(metaclass=abc.ABCMeta):
    """
    Abstract class to support calldata encoding for custom types

    Can be used to simplify code
    """

    @abc.abstractmethod
    def __to_calldata__(self) -> "Encodable":
        """
        Override this method to return calldata-compatible type

        .. warning::
                returning ``self`` may lead to an infinite loop or an exception
        """
        raise NotImplementedError()


type Decoded = None | int | str | bytes | list[Decoded] | dict[str, Decoded]
"""
Type that represents what type is coerced to after ``decode . encode``
"""

type Encodable = (
    None
    | int
    | str
    | Address
    | bool
    | bytes
    | collections.abc.Sequence[Encodable]
    | collections.abc.Mapping[str, Encodable]
    | CalldataEncodable
)
"""
Type that can be encoded into calldata
"""

type EncodableWithDefault[T] = Encodable | T
"""
Type that can be encoded into calldata, provided ``default`` function ``T -> Encodable``
"""


def encode_default_parameter(b):
    if not dataclasses.is_dataclass(b):
        return b
    assert not isinstance(b, type)

    return {field.name: getattr(b, field.name) for field in dataclasses.fields(b)}


def encode[
    T
](
    x: EncodableWithDefault[T],
    *,
    default: typing.Callable[
        [EncodableWithDefault[T]], Encodable
    ] = encode_default_parameter,
) -> bytes:
    """
    Encodes python object into calldata bytes

    :param default: function to be applied to each object recursively, it must return object encodable to calldata

    .. warning::
            All composite types in the end are coerced to :py:class:`dict` and :py:class:`list`, so custom type information is *not* be preserved.
            Such types include:

            #. :py:class:`CalldataEncodable`
            #. :py:mod:`dataclasses`
    """
    mem = bytearray()

    def append_uleb128(i):
        assert i >= 0
        if i == 0:
            mem.append(0)
        while i > 0:
            cur = i & 0x7F
            i = i >> 7
            if i > 0:
                cur |= 0x80
            mem.append(cur)

    def impl_dict(b: collections.abc.Mapping):
        keys = list(b.keys())
        keys.sort()
        le = len(keys)
        le = (le << 3) | TYPE_MAP
        append_uleb128(le)
        for k in keys:
            if not isinstance(k, str):
                raise TypeError(f"key is not string `{repr(k)}`")
            bts = k.encode("utf-8")
            append_uleb128(len(bts))
            mem.extend(bts)
            impl(b[k])

    def impl(b: EncodableWithDefault[T]):
        b = default(b)
        if isinstance(b, CalldataEncodable):
            b = b.__to_calldata__()
        if b is None:
            mem.append(SPECIAL_NULL)
        elif b is True:
            mem.append(SPECIAL_TRUE)
        elif b is False:
            mem.append(SPECIAL_FALSE)
        elif isinstance(b, int):
            if b >= 0:
                b = (b << 3) | TYPE_PINT
                append_uleb128(b)
            else:
                b = -b - 1
                b = (b << 3) | TYPE_NINT
                append_uleb128(b)
        elif isinstance(b, Address):
            mem.append(SPECIAL_ADDR)
            mem.extend(b.as_bytes)
        elif isinstance(b, (bytes, bytearray)):
            lb = len(b)
            lb = (lb << 3) | TYPE_BYTES
            append_uleb128(lb)
            mem.extend(b)
        elif isinstance(b, memoryview):
            mem.extend(b.tolist())
        elif isinstance(b, str):
            b = b.encode("utf-8")
            lb = len(b)
            lb = (lb << 3) | TYPE_STR
            append_uleb128(lb)
            mem.extend(b)
        elif isinstance(b, collections.abc.Sequence):
            lb = len(b)
            lb = (lb << 3) | TYPE_ARR
            append_uleb128(lb)
            for x in b:
                impl(x)
        elif isinstance(b, collections.abc.Mapping):
            impl_dict(b)
        else:
            raise TypeError(f"not calldata encodable {b!r}: {type(b)}")

    impl(x)
    return bytes(mem)


class DecodingError(ValueError):
    pass


def decode(
    mem0: collections.abc.Buffer,
    *,
    memview2bytes: typing.Callable[[memoryview], typing.Any] = bytes,
) -> Decoded:
    """
    Decodes calldata encoded bytes into python DSL

    Out of composite types it will contain only :py:class:`dict` and :py:class:`list`
    """
    mem: memoryview = memoryview(mem0)

    def fetch_mem(cnt: int) -> memoryview:
        nonlocal mem

        if len(mem) < cnt:
            raise DecodingError("unexpected end of memory")
        ret = mem[:cnt]
        mem = mem[cnt:]
        return ret

    def read_uleb128() -> int:
        nonlocal mem
        ret = 0
        off = 0
        while True:
            m = fetch_mem(1)[0]
            ret = ret | ((m & 0x7F) << off)
            if (m & 0x80) == 0:
                if m == 0 and off != 0:
                    raise DecodingError("most significant octet can not be zero")
                break
            off += 7
        return ret

    def impl() -> typing.Any:
        nonlocal mem
        code = read_uleb128()
        typ = code & 0x7
        if typ == TYPE_SPECIAL:
            if code == SPECIAL_NULL:
                return None
            if code == SPECIAL_FALSE:
                return False
            if code == SPECIAL_TRUE:
                return True
            if code == SPECIAL_ADDR:
                return Address(fetch_mem(Address.SIZE))
            raise DecodingError(f"Unknown special {bin(code)} {hex(code)}")
        code = code >> 3
        if typ == TYPE_PINT:
            return code
        elif typ == TYPE_NINT:
            return -code - 1
        elif typ == TYPE_BYTES:
            return memview2bytes(fetch_mem(code))
        elif typ == TYPE_STR:
            return str(fetch_mem(code), encoding="utf-8")
        elif typ == TYPE_ARR:
            ret_arr = []
            for _i in range(code):
                ret_arr.append(impl())
            return ret_arr
        elif typ == TYPE_MAP:
            ret_dict: dict[str, typing.Any] = {}
            prev = None
            for _i in range(code):
                le = read_uleb128()
                key = str(fetch_mem(le), encoding="utf-8")
                if prev is not None:
                    if prev >= key:
                        raise DecodingError(
                            f"unordered calldata keys: `{prev}` >= `{key}`"
                        )
                prev = key
                assert key not in ret_dict
                ret_dict[key] = impl()
            return ret_dict
        raise DecodingError(f"invalid type {typ}")

    res = impl()
    if len(mem) != 0:
        raise DecodingError(f"unparsed end {bytes(mem[:5])!r}... (decoded {res})")
    return res


def to_str(d: Encodable) -> str:
    """
    Transforms calldata DSL into human readable json-like format, should be used for debug purposes only
    """
    buf: list[str] = []

    def impl(d: Encodable) -> None:
        if d is None:
            buf.append("null")
        elif d is True:
            buf.append("true")
        elif d is False:
            buf.append("false")
        elif isinstance(d, str):
            buf.append(json.dumps(d))
        elif isinstance(d, (bytes, bytearray)):
            buf.append("b#")
            buf.append(d.hex())
        elif isinstance(d, memoryview):
            buf.append("b#")
            buf.append(d.hex())
        elif isinstance(d, int):
            buf.append(str(d))
        elif isinstance(d, Address):
            buf.append("addr#")
            buf.append(d.as_bytes.hex())
        elif isinstance(d, collections.abc.Mapping):
            buf.append("{")
            comma = False
            for k, v in d.items():
                if comma:
                    buf.append(",")
                comma = True
                buf.append(json.dumps(k))
                buf.append(":")
                impl(v)
            buf.append("}")
        elif isinstance(d, collections.abc.Sequence):
            buf.append("[")
            comma = False
            for v in d:
                if comma:
                    buf.append(",")
                comma = True
                impl(v)
            buf.append("]")
        elif isinstance(d, CalldataEncodable):
            impl(d.__to_calldata__())
        else:
            raise DecodingError(f"can't encode {d} to calldata")

    impl(d)
    return "".join(buf)
