import sys
import time

import interface_control as pc

dev = pc.Device()
print("device opened:", dev.dev)
print("detached kernel interfaces:", dev._detached)

state = pc.State()
for attempt in range(3):
    try:
        dev.poll(state)
        break
    except Exception as e:
        print("poll attempt", attempt, "failed:", e)
        time.sleep(0.2)

print("buttons: phantom=%d line=%d mute=%d mono=%d" % (state.phantom, state.line, state.mute, state.mono))
print("mic dBFS:", [round(pc.gain_to_db(x), 1) for x in state.mic])
print("bus dBFS:", [round(pc.gain_to_db(x), 1) for x in state.bus])

cmd = pc.Command()
try:
    cmd.set_output_fader(0, pc.Value.DB(-3.0))
    dev.send(cmd)
    print("send set_output_fader(0, -3dB): OK")
    cmd.set_input_fader(0, 0, pc.Channel.LEFT, pc.Value.DB(-6.0))
    dev.send(cmd)
    print("send set_input_fader(0,0,L,-6dB): OK")
except Exception as e:
    print("send FAILED:", e)

before = state.line
try:
    cmd.set_button(pc.Button.LINE, not before)
    dev.send(cmd)
    dev.poll(state)
    print("LINE toggle: before=%d after=%d" % (before, state.line))
    cmd.set_button(pc.Button.LINE, before)
    dev.send(cmd)
    dev.poll(state)
    print("LINE restored: %d" % state.line)
except Exception as e:
    print("button roundtrip FAILED:", e)

dev.close()
print("closed OK")
