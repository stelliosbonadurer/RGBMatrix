# Network Recovery Hardening Plan

Status: implementation complete; attended hardware acceptance pending

## Implemented baseline

The archived network plan delivered the NetworkManager state machine, candidate
profile rollback, protected setup AP, Unix-socket privilege boundary, web setup page,
and boot units. Provisioning from `SPhone` to `SpectrumSetup-5B` succeeded without
deleting the older profile.

## Implemented hardening

- Added a separate `gpiomon` recovery watcher for a five-second hold on BCM GPIO 19.
  The helper authorizes its `local_force_ap` request with Unix peer credentials, not
  a remotely reusable token.
- Moved AP and candidate WPA writes from `nmcli` argv to NetworkManager's system
  D-Bus. Candidate profile creation and its key are now one transaction.
- Added simulated coverage for boot fallback, sustained LAN loss, reconnection,
  DHCP-less candidates, candidate rollback, AP restart reconciliation, physical hold
  filtering, and root-peer authorization.
- Kept known-network retry explicit while AP mode is active. Periodic retry would
  disconnect setup clients on the single-radio target and make the recovery channel
  unpredictable.
- Kept captive DNS out of scope. `http://192.168.50.1:8080/setup` is authoritative.

## Remaining attended validation

1. With console or physical access available, make saved networks unreachable and
   verify the 60-second boot fallback and 120-second sustained-loss fallback.
2. Test wrong credentials, DHCP failure, successful setup-AP provisioning, reboot in
   both modes, and restoration after power loss.
3. Wire and exercise the GPIO 19 button, including a short accidental press and a
   valid five-second hold. Confirm the service still starts after reboot.

Do not archive this plan until those RF, DHCP, reboot, power-loss, and physical-input
checks pass on the real Pi. The exact procedure and wiring are in
[`../network-fallback-operations.md`](../network-fallback-operations.md).

## Acceptance

- The Pi recovers without SSH when all known networks are unavailable.
- Bad credentials restore the AP and preserve every previously working profile.
- Physical recovery works after reboot and cannot be triggered remotely without
  authorization.
