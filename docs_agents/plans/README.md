# Plans Index

These documents are implementation plans for future work. Nothing described here
should be assumed to exist until the relevant plan and acceptance checks are
completed.

## Separate plans

1. [Runtime architecture](runtime-architecture.md) — decide what should remain in
   Python, remove application dependence on the upstream demo layout, and define a
   common visualization contract.
2. [Portable Raspberry Pi deployment](portable-deployment.md) — make installation
   reproducible across supported Pi models and evaluate containers with measured
   latency rather than assuming they help.
3. [Web control service](web-control-service.md) — provide a phone-friendly service
   that starts and stops visualizations and exposes their supported parameters while
   preserving every CLI entry point.
4. [Known-network and fallback access point](network-fallback-ap.md) — join known
   Wi-Fi networks when possible and otherwise expose a setup SSID and provisioning
   page.

## How the plans relate

The plans are independently scoped, but they share these interfaces:

```text
visualization definitions + parameter schemas
                    |
             runtime/CLI contract
              /             \
     web supervisor       portable install
              \             /
          systemd services on the Pi
                    |
       LAN mode or fallback setup AP
```

- The runtime plan should establish the visualization registry and typed parameter
  schema before the full web UI is built.
- The deployment plan should establish supported hardware/OS combinations before a
  container or install image is selected.
- The network plan may be prototyped separately, but its captive/setup page should
  eventually be served by, or clearly linked to, the web service.
- The web service and network manager are separate services. A failure in either
  must not corrupt visualization configuration or leave an unmanaged child process.

## Suggested implementation order

The selected implementation sequence is:

1. Inventory current behavior and record the existing FFT latency as acceptable. No
   performance-measurement gate is required.
2. Implement the shared typed runtime/CLI contract and service-safe execution for the
   FFT and sorting visualizations.
3. Implement the allowlisted web supervisor/API MVP and boot-service template.

Those three steps are now implemented. The service is enabled on the current Pi, and
the API plus finite sorting/native-matrix path have been validated. See
[web-service-operations.md](../web-service-operations.md) for the exact boundary,
deployment state, and commands. The FFT audio path was not launched during this
deployment validation because its configured microphone was absent.

Saved for later, in this order unless priorities change:

1. Portable native installation and additional Pi support.
2. Phone-focused UI, live FFT IPC, presets, and pairing improvements.
3. Known-network selection and fallback AP provisioning.
4. Optional container feasibility work.

Current FFT latency is accepted as sufficient. Future work should not add a benchmark
gate unless performance visibly regresses or a supported Pi fails to render acceptably.
