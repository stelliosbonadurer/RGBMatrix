# Network Recovery Hardening Plan

Status: pending physical-access validation and recovery UX

## Implemented baseline

The archived network plan delivered the NetworkManager state machine, candidate
profile rollback, protected setup AP, Unix-socket privilege boundary, web setup page,
and boot units. Provisioning from `SPhone` to `SpectrumSetup-5B` succeeded without
deleting the older profile.

## Remaining work

1. With console or physical access available, make saved networks unreachable and
   verify the 60-second boot fallback and 120-second sustained-loss fallback.
2. Test wrong credentials, DHCP failure, successful setup-AP provisioning, reboot in
   both modes, and restoration after power loss.
3. Add a physical button or similarly local action that forces setup mode even when
   web credentials or saved profiles are unusable.
4. Decide whether AP mode should periodically retry known networks; today retry is an
   explicit authenticated operation.
5. Add captive DNS only if automatic phone portal discovery is worth the added DNS
   ownership. The fixed `http://192.168.50.1:8080/setup` URL remains authoritative.
6. Replace password-bearing `nmcli` argv with NetworkManager D-Bus secret handling as
   a defense-in-depth improvement against privileged local process inspection.

## Acceptance

- The Pi recovers without SSH when all known networks are unavailable.
- Bad credentials restore the AP and preserve every previously working profile.
- Physical recovery works after reboot and cannot be triggered remotely without
  authorization.
