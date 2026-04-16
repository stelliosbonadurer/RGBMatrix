# Mobile Control and Phone-Only Onboarding Strategy

Last updated: 2026-04-15
Status: Planning approved, implementation not started

## 1. Goal
Define a customer-ready strategy so users can:
- Control the device from a phone-friendly web page.
- Complete initial setup using only a phone (no laptop required).

This plan extends the performance-first control panel approach in `web_control_panel_plan.md`.

## 2. Product Principles
- Visualizer reliability and frame performance always take priority over control UX speed.
- Phone UI is convenience-focused and intentionally low-frequency for setting updates.
- Setup must be simple for non-technical users receiving a finished product.
- Device must be recoverable to setup mode without special tools.

## 3. Phone-Friendly Web UI Requirements
Mobile-first requirements:
- Responsive layout optimized for narrow screens first.
- Large tap targets and high-contrast controls.
- Clear section grouping (audio, visual, layers, presets, system).
- Sticky primary actions only where needed (Apply, Save Preset, Restore).

Performance-safe interaction model:
- Sliders visually move in browser while dragging.
- Actual device updates default to commit-on-release.
- Optional live mode can exist, but at low capped update rates.
- No FFT or frame stream to browser.

Resilience behavior:
- If control endpoint is unreachable, show offline state without spamming retries.
- Last known UI values are shown as cached hints, not guaranteed runtime truth.

## 4. Customer Onboarding Strategy (Phone-Only)
Primary strategy: SoftAP provisioning + captive portal.

First-boot flow:
1. Device enters Provisioning Mode automatically.
2. Device starts temporary setup Wi-Fi (example: Matrix-Setup-XXXX).
3. User connects phone to setup Wi-Fi.
4. Captive portal opens, or user manually visits printed setup URL.
5. User enters home Wi-Fi credentials.
6. Device validates and joins home network.
7. Success screen shows final local control URL and next steps.

Why this is the default:
- No app install required.
- Works with standard phone browser.
- Familiar IoT setup pattern with high usability.

## 5. Fallback and Recovery Paths
Fallback if captive portal does not auto-open:
- Printed manual URL for setup portal.
- QR code to setup instructions page.

Recovery:
- Physical reset/provision button long-press re-enters Provisioning Mode.
- Recovery should not require SD card access or terminal commands.

Recommended LED state language:
- Slow blink: Provisioning Mode active.
- Fast blink: Attempting Wi-Fi join.
- Solid: Connected and running.
- Distinct error pattern: Failed join, retry or reset.

## 6. Security Baseline for Distribution
Minimum security for give/sell scenario:
- Unique default setup password per unit.
- Unique device identifier/hostname per unit.
- Local network access by default (no internet exposure required).
- If remote access is ever enabled, require explicit opt-in and credentials hardening.

Operational safeguards:
- Do not print customer home Wi-Fi credentials in logs.
- Keep setup credentials and runtime admin credentials separate.

## 7. Runtime Mode Separation
Mode 1: Provisioning Mode
- Setup network + captive portal only.
- Limited controls for network setup and identity.

Mode 2: Runtime Mode
- Visualizer + local control page available on home network.
- Provisioning services disabled unless user triggers reset.

Hard requirement:
- Visualizer process remains independent from onboarding/control services.

## 8. Packaging and First-Use Materials
Include in box/device label:
- Setup Wi-Fi name pattern.
- Default setup password.
- QR code for setup instructions.
- Manual fallback URL.
- Quick reset instructions.

Instruction card should explain:
1. Connect to setup Wi-Fi.
2. Open setup page.
3. Enter home Wi-Fi.
4. Reconnect phone to home Wi-Fi.
5. Open control URL.

## 9. Suggested Future Documentation Artifacts
To complete before shipping units:
- End-user Quick Start card text.
- Support troubleshooting matrix for common phone issues.
- Factory reset behavior spec.
- Security policy for local-only vs remote modes.

## 10. Acceptance Criteria
A release is onboarding-ready only if:
- A new user can complete setup using phone only.
- Setup succeeds without SSH or terminal use.
- Visualizer runs normally when control/onboarding services are unavailable.
- Re-entry into Provisioning Mode is reliable via physical action.
- UI remains usable on common phone viewport sizes.

## 11. Open Decisions to Lock
- Final SSID naming convention and password policy.
- Captive portal implementation approach on Raspberry Pi OS.
- Exact reset button timing semantics.
- Whether to support optional BLE onboarding in a later phase.
