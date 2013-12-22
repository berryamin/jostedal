"""Implementation of RFC 5389 Session Traversal Utilities for NAT (STUN)
:see: http://tools.ietf.org/html/rfc5389
"""

import struct
import socket
from operator import itemgetter


# Comprehension-required range (0x0000-0x7FFF):
ATTRIBUTE_MAPPED_ADDRESS =      0x0001
ATTRIBUTE_USERNAME =            0x0006
ATTRIBUTE_MESSAGE_INTEGRITY =   0x0008
ATTRIBUTE_ERROR_CODE =          0x0009
ATTRIBUTE_UNKNOWN_ATTRIBUTES =  0x000A
ATTRIBUTE_REALM =               0x0014
ATTRIBUTE_NONCE =               0x0015
ATTRIBUTE_XOR_MAPPED_ADDRESS =  0x0020
# Comprehension-optional range (0x8000-0xFFFF)
ATTRIBUTE_SOFTWARE =            0x8022
ATTRIBUTE_ALTERNATE_SERVER =    0x8023
ATTRIBUTE_FINGERPRINT =         0x8028

FORMAT_STUN =       0b00
FORMAT_CHANNEL =    0b10

MAGIC_COOKIE = 0x2112A442

METHOD_BINDING = 0x001

CLASS_REQUEST =             0x00
CLASS_INDICATION =          0x01
CLASS_RESPONSE_SUCCESS =    0x10
CLASS_RESPONSE_ERROR =      0x11

class StunMessageParser(object):
    def __init__(self):
        self.buffer = ''


class StunMessage(tuple):
    """STUN message structure
    :see: http://tools.ietf.org/html/rfc5389#section-6
    """
    msg_method = property(itemgetter(0))
    msg_class = property(itemgetter(1))
    msg_length = property(itemgetter(2))
    magic_cookie = property(itemgetter(3))
    transaction_id = property(itemgetter(4))
    attributes = property(itemgetter(5))

    _HEADER_FORMAT = '>2HL12s'
    _HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)

    _ATTRIBUTE_FACTORIES = {}

    def __new__(cls, msg_method, msg_class, msg_length, magic_cookie,
                transaction_id, attributes):
        return tuple.__new__(cls, (msg_method, msg_class, msg_length,
                                   magic_cookie, transaction_id, attributes))

    @classmethod
    def decode(cls, data, offset=0, length=None):
        assert ord(data[offset]) >> 6 == FORMAT_STUN, \
            "Stun message MUST start with 0b00"
        fields = struct.unpack_from(cls._HEADER_FORMAT, data)
        (msg_type, msg_length, magic_cookie, transaction_id) = fields
        msg_type &= 0x3fff               # 00111111 11111111
        msg_method = msg_type & 0xfeef   # ..111110 11101111
        msg_class = msg_type >> 4 & 0x11 # ..000001 00010000
        offset += cls._HEADER_SIZE
        attributes = tuple(cls.decode_attributes(data, offset, msg_length))
        return cls(msg_method, msg_class, msg_length, magic_cookie,
                   transaction_id, attributes)

    @classmethod
    def decode_attributes(cls, data, offset, length):
        end = offset + length
        while offset < end:
            (attr_type, attr_length) = struct.unpack_from(
                StunMessageAttribute.HEADER_FORMAT, data, offset)
            offset += StunMessageAttribute.HEADER_SIZE
            factory = cls._ATTRIBUTE_FACTORIES.get(attr_type, UnknownAttribute)
            attr_value = factory.decode(data, offset, attr_length)
            yield factory(attr_type, attr_length, attr_value)
            padding = attr_length % 4
            offset += attr_length + padding

    @classmethod
    def add_attribute_factory(cls, attr_type, factory):
        assert not cls._ATTRIBUTE_FACTORIES.get(attr_type, False), \
            "Duplicate factory for {:#06x}".format(attr_type)
        cls._ATTRIBUTE_FACTORIES[attr_type] = factory

    def __len__(self):
        return self._HEADER_SIZE + self.msg_length

    def __repr__(self):
        return ("{}(method={:#05x}, class={:#04x}, length={}, magic_cookie={:#010x}, "
                "transaction_id={}, attributes={})".format(self.__class__,
                    self.msg_method, self.msg_class, self.msg_length,
                    self.magic_cookie, self.transaction_id.encode('hex'),
                    self.attributes))


class StunMessageAttribute(tuple):
    """STUN message attribute structure
    :see: http://tools.ietf.org/html/rfc5389#section-15
    """
    HEADER_FORMAT = '>2H'
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    type = property(itemgetter(0))
    length = property(itemgetter(1))
    value = property(itemgetter(2))

    def __new__(self, type_, length, value):
        return tuple.__new__(self, (type_, length, value))

    @classmethod
    def decode(cls, data, offset, length):
        return buffer(data, offset, length)

    def __str__(self):
        return "value={!r}".format(self.value)

    def __repr__(self, *args, **kwargs):
        return "{}(type={:#06x}, length={}, {})".format(
            self.__class__.__name__, self.type, self.length, str(self))


class UnknownAttribute(StunMessageAttribute):
    pass


def stunattribute(attribute_type, parser=StunMessage):
    """Decorator to add a Stun Attribute as an recognized attribute type
    """
    def _decorate(cls):
        cls.TYPE = attribute_type
        parser.add_attribute_factory(attribute_type, cls)
        return cls
    return _decorate


@stunattribute(ATTRIBUTE_MAPPED_ADDRESS)
class MappedAddress(StunMessageAttribute):
    """STUN MAPPED-ADDRESS attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.1
    """
    FAMILY_IPv4 = 0x01
    FAMILY_IPv6 = 0x02
    _FAMILY_MAP = {FAMILY_IPv4: socket.AF_INET,
               FAMILY_IPv6: socket.AF_INET6}

    _VALUE_HEADER_FORMAT = '>xBH'
    _VALUE_HEADER_SIZE = struct.calcsize(_VALUE_HEADER_FORMAT)

    @classmethod
    def decode(cls, data, offset, length):
        family, port = struct.unpack_from(cls._VALUE_HEADER_FORMAT, data, offset)
        offset += cls._VALUE_HEADER_SIZE
        address = buffer(data, offset, length - cls._VALUE_HEADER_SIZE)
        return (family, port, address)

    def __str__(self):
        family, port, address = self.value
        ipaddr = socket.inet_ntop(self._FAMILY_MAP[family], buffer(address))
        return "family={:#04x}, port={}, address={!r}".format(family, port, ipaddr)


@stunattribute(ATTRIBUTE_XOR_MAPPED_ADDRESS)
class XorMappedAddress(MappedAddress):
    """STUN XOR-MAPPED-ADDRESS attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.2
    """
    @classmethod
    def decode(cls, data, offset, length):
        family, xport = struct.unpack_from(cls._VALUE_HEADER_FORMAT, data, offset)
        offset += cls._VALUE_HEADER_SIZE
        if family == cls.FAMILY_IPv4:
            xaddress = buffer(data, offset, 4)
        elif family == cls.FAMILY_IPv6:
            xaddress = buffer(data, offset, 16)

        # xport and xaddress are xored with the concatination of
        # the magic cookie and the transaction id (data[4:20])
        magic = bytearray(*struct.unpack_from('>16s', data, 4))
        port = xport ^ magic[0] << 8 ^ magic[1]
        address = bytearray(ord(a) ^ b for a, b in zip(xaddress, magic))

        return (family, port, address)


@stunattribute(ATTRIBUTE_USERNAME)
class Username(StunMessageAttribute):
    """STUN USERNAME attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.3
    """
    @classmethod
    def decode(cls, data, offset, length):
        return str(buffer(data, offset, length)).decode('UTF-8')


@stunattribute(ATTRIBUTE_MESSAGE_INTEGRITY)
class MessageIntegrity(StunMessageAttribute):
    """STUN MESSAGE-INTEGRITY attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.4
    """


@stunattribute(ATTRIBUTE_FINGERPRINT)
class Fingerprint(StunMessageAttribute):
    """STUN FINGERPRINT attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.5
    """
    MAGIC = 0x5354554e

    def __str__(self):
        return str(self.value).encode('hex')


@stunattribute(ATTRIBUTE_ERROR_CODE)
class ErrorCode(StunMessageAttribute):
    """STUN ERROR-CODE attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.6
    """
    _VALUE_HEADER_FORMAT = '>2x2B'
    _VALUE_HEADER_SIZE = struct.calcsize(_VALUE_HEADER_FORMAT)

    @classmethod
    def decode(cls, data, offset, length):
        err_class, err_number = struct.unpack_from(cls._VALUE_HEADER_FORMAT, data, offset)
        err_class &= 0b111
        err_reason = str(buffer(data, offset, length)).decode('UTF-8')
        return (err_class, err_number, err_reason)

    def __str__(self):
        return "code={:1d}{:02d}, reason={!r}".format(*self)


@stunattribute(ATTRIBUTE_REALM)
class Realm(StunMessageAttribute):
    """STUN REALM attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.7
    """
    @classmethod
    def decode(cls, data, offset, length):
        return str(buffer(data, offset, length)).decode('UTF-8')


@stunattribute(ATTRIBUTE_NONCE)
class Nonce(StunMessageAttribute):
    """STUN NONCE attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.8
    """


@stunattribute(ATTRIBUTE_UNKNOWN_ATTRIBUTES)
class UnknownAttributes(StunMessageAttribute):
    """STUN UNKNOWN-ATTRIBUTES attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.9
    """
    @classmethod
    def decode(cls, data, offset, length):
        fmt = '>{}H'.format(length / 2)
        return struct.unpack_from(fmt, data, offset)


@stunattribute(ATTRIBUTE_SOFTWARE)
class Software(StunMessageAttribute):
    """STUN SOFTWARE attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.10
    """
    @classmethod
    def decode(cls, data, offset, length):
        return str(buffer(data, offset, length)).decode('UTF-8')


@stunattribute(ATTRIBUTE_ALTERNATE_SERVER)
class AlternateServer(MappedAddress):
    """STUN ALTERNATE-SERVER attribute
    :see: http://tools.ietf.org/html/rfc5389#section-15.11
    """


# ----------------------------
# RFC 5766 TURN
# ----------------------------
ATTRIBUTE_CHANNEL_NUMBER =      0x000C
ATTRIBUTE_LIFETIME =            0x000D
ATTRIBUTE_XOR_PEER_ADDRESS =    0x0012
ATTRIBUTE_DATA =                0x0013
ATTRIBUTE_XOR_RELAYED_ADDRESS = 0x0016
ATTRIBUTE_EVEN_PORT =           0x0018
ATTRIBUTE_REQUESTED_TRANSPORT = 0x0019
ATTRIBUTE_DONT_FRAGMENT =       0x001A
ATTRIBUTE_RESERVATION_TOKEN =   0x0022


@stunattribute(ATTRIBUTE_CHANNEL_NUMBER)
class ChannelNumber(StunMessageAttribute):
    """TURN STUN CHANNEL-NUMBER attribute
    :see: http://tools.ietf.org/html/rfc5766#section-14.1
    """
    @classmethod
    def decode(cls, data, offset, length):
        return struct.unpack_from('>H2x', data, offset)


@stunattribute(ATTRIBUTE_LIFETIME)
class Lifetime(StunMessageAttribute):
    """TURN STUN LIFETIME attribute
    :see: http://tools.ietf.org/html/rfc5766#section-14.2
    """


@stunattribute(ATTRIBUTE_XOR_PEER_ADDRESS)
class XorPeerAddress(XorMappedAddress):
    """TURN STUN XOR-PEER-ADDRESS attribute
    :see: http://tools.ietf.org/html/rfc5766#section-14.3
    """


@stunattribute(ATTRIBUTE_DATA)
class Data(StunMessageAttribute):
    """TURN STUN DATA attribute
    :see: http://tools.ietf.org/html/rfc5766#section-14.4
    """


@stunattribute(ATTRIBUTE_XOR_RELAYED_ADDRESS)
class XorRelayedAddress(XorMappedAddress):
    """TURN STUN XOR-RELAYED-ADDRESS attribute
    :see: http://tools.ietf.org/html/rfc5766#section-14.5
    """


@stunattribute(ATTRIBUTE_EVEN_PORT)
class EvenPort(StunMessageAttribute):
    """TURN STUN EVEN-PORT attribute
    :see: http://tools.ietf.org/html/rfc5766#section-14.6
    """
    RESERVE = 0b10000000

    @classmethod
    def decode(cls, data, offset, length):
        return struct.unpack_from('>B', data, offset)[0] & 0b10000000


@stunattribute(ATTRIBUTE_REQUESTED_TRANSPORT)
class RequestedTransport(StunMessageAttribute):
    """TURN STUN REQUESTED-TRANSPORT attribute
    :see: http://tools.ietf.org/html/rfc5766#section-14.7
    """
    @classmethod
    def decode(cls, data, offset, length):
        protocol, = struct.unpack_from('>B3x', data, offset)
        return protocol



"""NAT Behavior Discovery Using Session Traversal Utilities for NAT (STUN)
:see: http://tools.ietf.org/html/rfc5780
"""

# Comprehension-required range (0x0000-0x7FFF):
ATTRIBUTE_CHANGE_REQUEST =    0x0003
ATTRIBUTE_PADDING =           0x0026
ATTRIBUTE_RESPONSE_PORT =     0x0027
# Comprehension-optional range (0x8000-0xFFFF):
ATTRIBUTE_RESPONSE_ORIGIN =   0x802b
ATTRIBUTE_OTHER_ADDRESS =     0x802c


@stunattribute(ATTRIBUTE_CHANGE_REQUEST)
class ChangeRequest(StunMessageAttribute):
    """
    :see: http://tools.ietf.org/html/rfc5780#section-7.2
    """
    @classmethod
    def decode(cls, data, offset, length):
        flags, = struct.unpack_from('>L', data, offset)
        change_ip =     flags & 0b0100
        change_port =   flags & 0b0010
        return (change_ip, change_port)


@stunattribute(ATTRIBUTE_RESPONSE_ORIGIN)
class ResponseOrigin(MappedAddress):
    """
    :see: http://tools.ietf.org/html/rfc5780#section-7.3
    """


@stunattribute(ATTRIBUTE_OTHER_ADDRESS)
class OtherAddress(MappedAddress):
    """
    :see: http://tools.ietf.org/html/rfc5780#section-7.4
    """


@stunattribute(ATTRIBUTE_RESPONSE_PORT)
class ResponsePort(StunMessageAttribute):
    """
    :see: http://tools.ietf.org/html/rfc5780#section-7.5
    """
    @classmethod
    def decode(cls, data, offset, length):
        port, = struct.unpack_from('>H2x', data, offset)
        return port


@stunattribute(ATTRIBUTE_PADDING)
class Padding(StunMessageAttribute):
    """
    :see: http://tools.ietf.org/html/rfc5780#section-7.6
    """



msg_data = str(bytearray.fromhex(
    "010100582112a4427a2f2b504c6a7457"
    "52616c5600200008000191170f01b020"
    "000100080001b0052e131462802b0008"
    "00010d960af0d7b4802c000800010d97"
    "0af0d7b48022001a4369747269782d31"
    "2e382e372e302027426c61636b20446f"
    "7727424e80280004aea90559"))
msg = StunMessage.decode(msg_data)
print repr(msg[:-1])
for attribute in msg.attributes:
    print repr(attribute)

