import struct
import sys

in_file = open("/dev/input/event0", "rb")

FORMAT = 'llHHI'
EVENT_SIZE = struct.calcsize(FORMAT)

event = in_file.read(EVENT_SIZE)

while event:
        (tv_sec, tv_usec, type, code, value) = struct.unpack(FORMAT, event)
        print "type %d code %d value %d\r\n" % (type, code, value)

        event = in_file.read(EVENT_SIZE)
in_file.close()
