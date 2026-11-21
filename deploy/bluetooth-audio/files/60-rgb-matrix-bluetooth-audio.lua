-- RGB Matrix Bluetooth receive bridge for WirePlumber 0.4.x.
-- The playback side is owned by PipeWire; the FFT opens hw:10,1 directly.

alsa_monitor.rules = alsa_monitor.rules or {}

table.insert(alsa_monitor.rules, {
  matches = {
    {
      { "node.name", "matches", "alsa_output.platform-snd_aloop.*" },
    },
  },
  apply_properties = {
    ["node.description"] = "Bluetooth loopback (RGB Matrix)",
    ["node.nick"] = "Bluetooth loopback (RGB Matrix)",
    ["priority.session"] = 2000,
    ["priority.driver"] = 2000,
    ["audio.channels"] = 1,
    ["audio.position"] = "MONO",
    ["audio.rate"] = 44100,
    ["audio.format"] = "S32LE",
    ["session.suspend-timeout-seconds"] = 0,
  },
})

-- PipeWire must not claim the capture half that PortAudio reads. Disabling only
-- these loopback input nodes leaves every physical microphone unchanged.
table.insert(alsa_monitor.rules, {
  matches = {
    {
      { "node.name", "matches", "alsa_input.platform-snd_aloop.*" },
    },
  },
  apply_properties = {
    ["node.disabled"] = true,
  },
})
