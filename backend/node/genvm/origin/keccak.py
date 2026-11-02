# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

# NOTE: source code from https://github.com/ctz/keccak
# some code was truncated for use in this project
"""
Implements keccak hash

Source code is taken from `https://github.com/ctz/keccak <https://github.com/ctz/keccak>`_ (Apache 2.0 license)
"""

__all__ = ("Keccak256", "KeccakHash")

import collections.abc
from copy import deepcopy
from functools import reduce
from math import log
from operator import xor

# The Keccak-f round constants.
RoundConstants = [
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
]

RotationConstants = [
    [0, 1, 62, 28, 27],
    [36, 44, 6, 55, 20],
    [3, 10, 43, 25, 39],
    [41, 45, 15, 21, 8],
    [18, 2, 61, 56, 14],
]

Masks = [(1 << i) - 1 for i in range(65)]


def bits2bytes(x):
    return (int(x) + 7) // 8


def rol(value, left, bits):
    """
    Circularly rotate 'value' to the left,
    treating it as a quantity of the given size in bits.
    """
    top = value >> (bits - left)
    bot = (value & Masks[bits - left]) << left
    return bot | top


def ror(value, right, bits):
    """
    Circularly rotate 'value' to the right,
    treating it as a quantity of the given size in bits.
    """
    top = value >> right
    bot = (value & Masks[right]) << (bits - right)
    return bot | top


def multirate_padding(used_bytes, align_bytes):
    """
    The Keccak padding function.
    """
    padlen = align_bytes - used_bytes
    if padlen == 0:
        padlen = align_bytes
    # note: padding done in 'internal bit ordering', wherein LSB is leftmost
    if padlen == 1:
        return [0x81]
    else:
        return [0x01] + ([0x00] * (padlen - 2)) + [0x80]


def sha_padding(used_bytes, align_bytes):
    """
    The SHA3 padding function
    """
    padlen = align_bytes - (used_bytes % align_bytes)
    if padlen == 1:
        return [0x86]
    elif padlen == 2:
        return [0x06, 0x80]
    else:
        return [0x06] + ([0x00] * (padlen - 2)) + [0x80]


def shake_padding(used_bytes, align_bytes):
    """
    The SHAKE padding function
    """
    padlen = align_bytes - (used_bytes % align_bytes)
    if padlen == 1:
        return [0x9F]
    elif padlen == 2:
        return [0x1F, 0x80]
    else:
        return [0x1F] + ([0x00] * (padlen - 2)) + [0x80]


def keccak_f(state):
    """
    This is Keccak-f permutation.  It operates on and
    mutates the passed-in KeccakState.  It returns nothing.
    """

    def keccak_round(a, rc):
        w, h = state.W, state.H
        rangew, rangeh = state.rangeW, state.rangeH
        lanew = state.lanew
        zero = state.zero

        # theta
        c = [reduce(xor, a[x]) for x in rangew]
        d = [0] * w
        for x in rangew:
            d[x] = c[(x - 1) % w] ^ rol(c[(x + 1) % w], 1, lanew)
            for y in rangeh:
                a[x][y] ^= d[x]

        # rho and pi
        b = zero()
        for x in rangew:
            for y in rangeh:
                b[y % w][(2 * x + 3 * y) % h] = rol(
                    a[x][y], RotationConstants[y][x], lanew
                )

        # chi
        for x in rangew:
            for y in rangeh:
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % w][y]) & b[(x + 2) % w][y])

        # iota
        a[0][0] ^= rc

    nr = 12 + 2 * int(log(state.lanew, 2))

    for ir in range(nr):
        keccak_round(state.s, RoundConstants[ir])


class KeccakState:
    """
    A keccak state container.

    The state is stored as a 5x5 table of integers.
    """

    __slots__ = ("lanew", "bitrate", "b", "s", "bitrate_bytes")

    W = 5
    H = 5

    rangeW = range(W)
    rangeH = range(H)

    @staticmethod
    def zero():
        """
        Returns an zero state table.
        """
        return [[0] * KeccakState.W for _ in KeccakState.rangeH]

    @staticmethod
    def format(st):
        """
        Formats the given state as hex, in natural byte order.
        """
        rows = []

        def fmt(stx):
            return "%016x" % stx

        for y in KeccakState.rangeH:
            row = []
            for x in KeccakState.rangeW:
                row.append(fmt(st[x][y]))
            rows.append(" ".join(row))
        return "\n".join(rows)

    @staticmethod
    def lane2bytes(s, w):
        """
        Converts the lane s to a sequence of byte values,
        assuming a lane is w bits.
        """
        o = []
        for b in range(0, w, 8):
            o.append((s >> b) & 0xFF)
        return o

    @staticmethod
    def bytes2lane(bb):
        """
        Converts a sequence of byte values to a lane.
        """
        r = 0
        for b in reversed(bb):
            r = r << 8 | b
        return r

    @staticmethod
    def ilist2bytes(bb):
        """
        Converts a sequence of byte values to a bytestring.
        """
        return bytes(bb)

    @staticmethod
    def bytes2ilist(ss):
        """
        Converts a string or bytestring to a sequence of byte values.
        """
        return map(ord, ss) if isinstance(ss, str) else list(ss)

    def __init__(self, bitrate, b):
        self.bitrate = bitrate
        self.b = b

        # only byte-aligned
        assert self.bitrate % 8 == 0
        self.bitrate_bytes = bits2bytes(self.bitrate)

        assert self.b % 25 == 0
        self.lanew = self.b // 25

        self.s = KeccakState.zero()

    def __str__(self):
        return KeccakState.format(self.s)

    def absorb(self, bb):
        """
        Mixes in the given bitrate-length string to the state.
        """
        assert len(bb) == self.bitrate_bytes

        bb += [0] * bits2bytes(self.b - self.bitrate)
        i = 0

        for y in self.rangeH:
            for x in self.rangeW:
                self.s[x][y] ^= KeccakState.bytes2lane(bb[i : i + 8])
                i += 8

    def squeeze(self):
        """
        Returns the bitrate-length prefix of the state to be output.
        """
        return self.get_bytes()[: self.bitrate_bytes]

    def get_bytes(self):
        """
        Convert whole state to a byte string.
        """
        out = [0] * bits2bytes(self.b)
        i = 0
        for y in self.rangeH:
            for x in self.rangeW:
                v = KeccakState.lane2bytes(self.s[x][y], self.lanew)
                out[i : i + 8] = v
                i += 8
        return out

    def set_bytes(self, bb):
        """
        Set whole state from byte string, which is assumed
        to be the correct length.
        """
        i = 0
        for y in self.rangeH:
            for x in self.rangeW:
                self.s[x][y] = KeccakState.bytes2lane(bb[i : i + 8])
                i += 8


class KeccakSponge:
    __slots__ = ("state", "padfn", "permfn", "buffer")

    def __init__(self, bitrate, width, padfn, permfn):
        self.state = KeccakState(bitrate, width)
        self.padfn = padfn
        self.permfn = permfn
        self.buffer = []

    def copy(self):
        return deepcopy(self)

    def absorb_block(self, bb):
        assert len(bb) == self.state.bitrate_bytes
        self.state.absorb(bb)
        self.permfn(self.state)

    def absorb(self, s):
        self.buffer += s

        while len(self.buffer) >= self.state.bitrate_bytes:
            self.absorb_block(self.buffer[: self.state.bitrate_bytes])
            self.buffer = self.buffer[self.state.bitrate_bytes :]

    def absorb_final(self):
        padded = self.buffer + self.padfn(len(self.buffer), self.state.bitrate_bytes)
        self.absorb_block(padded)
        self.buffer = []

    def squeeze_once(self):
        rc = self.state.squeeze()
        self.permfn(self.state)
        return rc

    def squeeze(self, l):
        z = self.squeeze_once()
        while len(z) < l:
            z += self.squeeze_once()
        return z[:l]


class KeccakHash:
    """
    The Keccak hash function, with a hashlib-compatible interface.
    """

    __slots__ = ("sponge", "digest_size", "block_size")

    def __init__(self, bitrate_bits: int, capacity_bits: int, output_bits: int):
        """
        Create a new Keccak hash instance.

        :param bitrate_bits: bitrate in bits
        :param capacity_bits: capacity in bits
        :param output_bits: output length in bits (must be divisible by 8)
        """
        # our in-absorption sponge. this is never given padding
        assert bitrate_bits + capacity_bits in (25, 50, 100, 200, 400, 800, 1600)
        self.sponge = KeccakSponge(
            bitrate_bits, bitrate_bits + capacity_bits, multirate_padding, keccak_f
        )

        # hashlib interface members
        assert output_bits % 8 == 0
        self.digest_size = bits2bytes(output_bits)
        self.block_size = bits2bytes(bitrate_bits)

    def __repr__(self):
        inf = (
            self.sponge.state.bitrate,
            self.sponge.state.b - self.sponge.state.bitrate,
            self.digest_size * 8,
        )
        return "<KeccakHash with r=%d, c=%d, image=%d>" % inf

    def copy(self) -> "KeccakHash":
        """Return a copy of this hash object."""
        return deepcopy(self)

    def update(self, s: collections.abc.Buffer, /) -> None:
        """
        Feed data into the hash.

        :param s: bytes-like data to absorb
        """
        self.sponge.absorb(s)

    def digest(self) -> bytes:
        """
        Return the digest of the data fed so far.

        :returns: hash digest as bytes
        """
        finalised = self.sponge.copy()
        finalised.absorb_final()
        digest = finalised.squeeze(self.digest_size)
        return KeccakState.ilist2bytes(digest)

    def hexdigest(self) -> str:
        """
        Return the hex-encoded digest of the data fed so far.

        :returns: hash digest as hex string
        """
        return self.digest().hex()

    @staticmethod
    def preset(bitrate_bits, capacity_bits, output_bits):
        """
        Returns a factory function for the given bitrate, sponge capacity and output length.
        The function accepts an optional initial input, ala hashlib.
        """

        def create(initial_input: collections.abc.Buffer | None = None):
            h = KeccakHash(bitrate_bits, capacity_bits, output_bits)
            if initial_input is not None:
                h.update(initial_input)
            return h

        return create


Keccak256 = KeccakHash.preset(1088, 512, 256)
"""
Default preset for Keccak hash that is the same as one used in eth
"""
