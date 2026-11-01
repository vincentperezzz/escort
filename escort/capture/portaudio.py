"""One process-wide lock around PortAudio's global state.

PortAudio is not thread-safe: Pa_Initialize, Pa_Terminate, Pa_OpenStream and
Pa_CloseStream all touch a process-global init count and open-stream list, and
PyAudio releases the GIL around them. Escort calls them from several threads at
once (every /api/status poll lists devices with its own PyAudio() while the
live audio thread opens, reads and closes its streams), which crashed the app
with an access violation (0xc0000005, jump to address 0) inside
_portaudiowpatch. Every PyAudio()/terminate()/open()/close() and device
enumeration must hold LOCK. Stream reads do not need it.
"""

from __future__ import annotations

import threading

LOCK = threading.RLock()
