import glob
import io
import os
import errno
import struct
from collections import namedtuple
import threading
import time
import select
import queue

TOUCH_X = 0
TOUCH_Y = 1

TouchEvent = namedtuple('TouchEvent', ('timestamp', 'type', 'code', 'value'))

EV_SYN = 0
EV_ABS = 3

ABS_X = 0
ABS_Y = 1

ABS_MT_SLOT = 0x2f # 47 MT slot being modified
ABS_MT_POSITION_X = 0x35 # 53 Center X of multi touch position
ABS_MT_POSITION_Y = 0x36 # 54 Center Y of multi touch position
ABS_MT_TRACKING_ID = 0x39 # 57 Unique ID of initiated contact

TS_PRESS = 1
TS_RELEASE = 0
TS_MOVE = 2

class Touch(object):
    def __init__(self, slot, x, y):
        self.slot = slot

        self._x = x
        self._y = y
        self.last_x = -1
        self.last_y = -1

        self._id = -1
        self.events = []
        self.on_move = None
        self.on_press = None
        self.on_release = None
        
    @property
    def position(self):
        return (self.x, self.y)

    @property
    def last_position(self):
        return (self.last_x, self.last_y)

    @property
    def valid(self):
        return self.id > -1

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        if value != self._id:
            if value == -1 and not TS_RELEASE in self.events:
                self.events.append(TS_RELEASE)    
            elif not TS_PRESS in self.events:
                self.events.append(TS_PRESS)

        self._id = value

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        if value != self._x and not TS_MOVE in self.events:
            self.events.append(TS_MOVE)
        self.last_x = self._x
        self._x = value

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        if value != self._y and not TS_MOVE in self.events:
            self.events.append(TS_MOVE)
        self.last_y = self._y
        self._y = value

    def handle_events(self):
        """Run outstanding press/release/move events"""
        for event in self.events:
            if event == TS_MOVE and callable(self.on_move):
                self.on_move(event, self)
            if event == TS_PRESS and callable(self.on_press):
                self.on_press(event, self)
            if event == TS_RELEASE and callable(self.on_release):
                self.on_release(event, self)

        self.events = []


class Touches(list):
    @property
    def valid(self):
        return [touch for touch in self if touch.valid]

class Touchscreen(object):

    TOUCHSCREEN_EVDEV_NAME = 'raspberrypi-ts'
    EVENT_FORMAT = str('llHHi')
    EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

    def __init__(self, device=None):
        self._device = self.TOUCHSCREEN_EVDEV_NAME if device is None else device
        self._running = False
        self._thread = None
        self._f_poll = select.poll()
        self._f_device = io.open(self._touch_device(), 'rb', self.EVENT_SIZE)
        self._f_poll.register(self._f_device, select.POLLIN)
        self.position = Touch(0, 0, 0)
        self.touches = Touches([Touch(x, 0, 0) for x in range(10)])
        self._event_queue = queue.Queue()
        self._touch_slot = 0

    def _run(self):
        self._running = True
        while self._running:
            self.poll()
            #time.sleep(0.0001)

    def run(self):
        if self._thread is not None:
            return

        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self):
        if self._thread is None:
            return
 
        self._running = False
        self._thread.join()
        self._thread = None

    @property
    def _current_touch(self):
        return self.touches[self._touch_slot]

    def close(self):
        self._f_device.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.close()

    def __iter__(self):
        pass

    def _lazy_read(self):
        while self._wait_for_events():
            event = self._f_device.read(self.EVENT_SIZE)
            if not event:
                break
            yield event

    def _get_pending_events(self):
        for event in self._lazy_read():
            (tv_sec, tv_usec, type, code, value) = struct.unpack(self.EVENT_FORMAT, event)
            self._event_queue.put(TouchEvent(tv_sec + (tv_usec / 1000000), type, code, value))

    def _wait_for_events(self, timeout=2):
        return self._f_poll.poll(timeout)

    def poll(self):
        self._get_pending_events()

        while not self._event_queue.empty():
            event = self._event_queue.get()
            self._event_queue.task_done()

            if event.type == EV_SYN: # Sync
                for touch in self.touches:
                    touch.handle_events()
                return self.touches
                
            if event.type == EV_ABS: # Absolute cursor position
                if event.code == ABS_MT_SLOT:
                    self._touch_slot = event.value
            
                if event.code == ABS_MT_TRACKING_ID: 
                    self._current_touch.id = event.value
            
                if event.code == ABS_MT_POSITION_X:
                    self._current_touch.x = event.value
            
                if event.code == ABS_MT_POSITION_Y:
                    self._current_touch.y = event.value
            
                if event.code == ABS_X:
                    self.position.x = event.value
            
                if event.code == ABS_Y:
                    self.position.y = event.value

        return []

    def _touch_device(self):
        for evdev in glob.glob("/sys/class/input/event*"):
            try:
                with io.open(os.path.join(evdev, 'device', 'name'), 'r') as f:
                    if f.read().strip() == self._device:
                        return os.path.join('/dev','input',os.path.basename(evdev))
            except IOError as e:
                if e.errno != errno.ENOENT:
                    raise
        raise RuntimeError('Unable to locate touchscreen device: {}'.format(self._device))

    def read(self):
        return next(iter(self))


if __name__ == "__main__":
    import signal

    ts = Touchscreen()

    def handle_event(event, touch):
        print(["Release","Press","Move"][event],
            touch.slot,
            touch.x,
            touch.y)

    for touch in ts.touches:
        touch.on_press = handle_event
        touch.on_release = handle_event
        touch.on_move = handle_event

    ts.run()

    try:
        signal.pause()
    except KeyboardInterrupt:
        print("Stopping thread...")
        ts.stop()
        exit()
