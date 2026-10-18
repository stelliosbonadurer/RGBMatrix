# 03 — Known-Network and Fallback Access Point Plan

Status: archived — controller, provisioning flow, and boot services implemented for
the current Bookworm/NetworkManager Pi on 2026-10-18

Physical fallback validation and recovery UX are tracked in
[network-recovery-hardening.md](../plans/network-recovery-hardening.md).

## Goal

At boot, the Pi should try saved Wi-Fi networks. If none is usable after a bounded
attempt window, it should create its own secured setup SSID so a phone can connect,
open a local page, supply network credentials, and reach the visualization controls.

This network lifecycle must work without SSH and must recover from bad credentials,
temporary outages, reboots, and power loss.

## Dependencies and boundaries

- [Portable deployment](../plans/portable-deployment.md) chooses the supported OS and network
  manager and installs the required units.
- [Web control](02-web-control-service.md) supplies the phone-facing control surface;
  this plan supplies connectivity and a narrowly privileged provisioning path.
- [Runtime architecture](01-runtime-architecture.md) must remain able to launch and run
  without LAN or internet access.

## Scope and assumptions

Selected implementation values are one `wlan0` interface, NetworkManager 1.42+, a
60-second boot grace, 120-second sustained-loss grace, 5/15/30-second retry pacing,
a 45-second candidate timeout, and `192.168.50.1/24`. “Connected” means
NetworkManager reports an active non-AP profile with a local IPv4 address; internet
access is deliberately not part of health.

- Target Raspberry Pi OS and its network stack must be fixed by the portable
  deployment plan before implementation. Prefer NetworkManager profiles and APIs if
  the supported OS uses NetworkManager; do not mix NetworkManager, `dhcpcd`, and
  hand-written interface configuration on the same image.
- Use one Wi-Fi interface initially. Simultaneous client and AP operation is not a
  requirement because chipset/driver support varies; treat normal LAN and setup AP as
  explicit modes.
- The visualization web service and network provisioning controller are separate
  responsibilities. The control page should remain available in both modes, while
  credential-changing endpoints are enabled only in setup/recovery mode.

## State machine

```text
BOOT
  -> TRY_KNOWN_NETWORKS
       -> LAN_CONNECTED
       -> START_SETUP_AP (after bounded failure)
            -> AP_READY
            -> VALIDATE_NEW_CREDENTIALS
                 -> LAN_CONNECTED
                 -> AP_READY (with useful error)

LAN_CONNECTED -- sustained loss --> RETRY_KNOWN_NETWORKS --> AP_READY if exhausted
physical recovery action -------------------------------> AP_READY
```

Every transition needs a timeout, bounded retries/backoff, journal event, and a clear
status exposed to the local UI. Avoid rapid LAN/AP flapping during a brief outage.

## Boot and recovery policy

1. Let the system network manager auto-connect saved profiles by priority.
2. Wait a configurable grace period for a usable connection, not merely association.
3. If no known connection succeeds, activate a project-owned AP profile with a fixed
   private subnet and DHCP/DNS service supplied through the chosen network stack.
4. Serve the setup/control page at a documented fixed IP and local hostname.
5. When credentials are submitted, create a candidate profile without deleting the
   last working profiles; deactivate AP, attempt the candidate, and verify it.
6. On success, retain the profile and advertise the normal control address. On
   failure, return to AP mode and display a sanitized reason.
7. After sustained loss in normal operation, retry known profiles before enabling AP.

The design must account for a phone disconnecting when AP mode shuts down. The page
should explain that the user must rejoin the home network and then open the device's
hostname/address; it cannot rely on the original HTTP request remaining alive.

## Setup AP and captive portal

- SSID pattern: a recognizable name plus a short non-sensitive device suffix, such
  as `RGB-Matrix-AB12`.
- Require WPA2/WPA3-compatible protection supported by the target stack. Do not ship
  a universal password; provision a per-device secret or an explicit physical
  pairing workflow.
- Redirect common captive-portal probes to a minimal local setup page where feasible,
  but always publish a manual fixed URL because phone captive detection is not
  consistent.
- Captive DNS should operate only in AP mode and must not alter normal LAN DNS.
- Do not require internet connectivity to configure or control the matrix.

## Credential handling and security

- Submit credentials only to the local device and never log the passphrase.
- Use the network manager's protected connection store rather than a project-readable
  plaintext JSON file.
- Restrict profile creation/deletion to a narrow privileged helper or controlled
  system service; the general web process remains unprivileged.
- Validate SSID/passphrase lengths and types, and pass them as structured API/DBus
  values rather than interpolated shell commands.
- Require physical presence, the setup credential, or an existing administrator
  session before changing networks.
- Provide explicit forget-network behavior with confirmation. Never delete the last
  working profile until replacement credentials are verified.

## Service ordering

The network controller should start after the base network manager and before
`network-online.target` assumptions can deadlock it. The web control service should
start without requiring internet or normal LAN connectivity and bind when either the
LAN or AP interface is available. Visualization launching must not depend on an
external network connection.

Exact systemd dependencies and NetworkManager dispatcher use must be proven on the
chosen OS image. Do not add an unbounded `network-online.target` wait to the visualizer
path.

## Implementation phases

### Phase 1: target and failure policy

1. Select the supported OS/network manager and inventory Wi-Fi hardware behavior on
   every supported Pi.
2. Define connection grace period, sustained-loss period, retry/backoff, profile
   priority, and what counts as “connected.”
3. Select AP subnet, SSID/secret provisioning, physical recovery action, and indicator
   behavior.

### Phase 2: command-line prototype

1. Create saved test profiles and an AP profile through the chosen network manager.
2. Implement an idempotent state controller using structured network-manager APIs.
3. Verify known-network boot, no-network fallback, bad password recovery, AP restart,
   and reboot during each transition.
4. Test with the actual single Wi-Fi interface on all supported boards.

### Phase 3: provisioning API/UI

1. Expose scan results, current mode, and credential submission through a narrow local
   API.
2. Add the mobile setup flow and captive-portal/manual URL behavior.
3. Add sanitized errors and instructions for moving the phone from setup AP to LAN.
4. Add authenticated network management in normal control mode.

### Phase 4: production hardening

1. Add a physical method to re-enter setup mode and test accidental activation.
2. Test loss/restoration, hidden SSIDs if supported, duplicate SSIDs, wrong passwords,
   DHCP failure, no internet, and power loss.
3. Bound logs and retry rates; verify secrets never appear in journald or API output.
4. Document factory reset separately from “forget current network.”

## Acceptance criteria

- With a reachable saved network, the Pi joins it and does not expose the setup AP.
- With no reachable saved network, a phone can discover the secured AP and load the
  setup page without internet or SSH.
- Valid submitted credentials survive reboot and return the device to normal LAN mode.
- Invalid credentials reliably return the device to AP mode without erasing older
  working profiles or exposing the passphrase.
- A brief Wi-Fi interruption does not cause rapid mode flapping; a sustained loss
  eventually produces a recoverable AP.
- The control service is reachable in both modes, and visualization output continues
  through network transitions unless a documented hardware constraint prevents it.
- Physical recovery works even when stored profiles and web authentication are wrong.

## Open decisions

- Physical recovery input and user-visible status indicator remain open.
- Automatic periodic return from an active AP to a previously known LAN is deferred;
  the setup API exposes an explicit retry operation.
- Captive DNS is deferred. Probe redirects plus the fixed manual URL are implemented.
- See [network-fallback-operations.md](../network-fallback-operations.md) for selected
  target values, deployment, and the remaining hardware acceptance matrix.
