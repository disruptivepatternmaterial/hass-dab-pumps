# De-clouding DAB pumps: evidence and plan

Status: **🚧 NOT YET DEPLOYED — research record only.** Nothing in this document is
implemented in this repo. The integration as shipped talks exclusively to DAB's cloud
(`dabsso.dabpumps.com`, `api.eu.dabcs.it`, `dconnect.dabpumps.com`, WAMP push over
`wss://dconnect.dabpumps.com/wsapp`; see `pydabpumps/const.py` in `pydabpumps==2.1.9`).

Goal: read and control the pump with no DAB account, no subscription, and no traffic to
DAB servers.

## What is known to exist on the hardware

Two local interfaces have been documented, one by DAB and one by the community. Which one
applies depends on the pump model, so step 1 is identifying the hardware (see
"Open questions").

### A. BLE "local connection" on H2D-era pumps (Esybox, Esybox Mini 3, Esybox Max, …)

Evidence:

- HA community thread [DAB Pumps integration via DAB Live](https://community.home-assistant.io/t/dab-pumps-integration-via-dab-live/708828), posts 19–21 (Aug 2026, user *Spaco*): BLE traffic from the H2D app was sniffed; the pump exposes a JSON REST API tunnelled over GATT.
- Working ESPHome proof of concept: [Vrobenmat/DAB-ESPHome](https://github.com/Vrobenmat/DAB-ESPHome) (`DAB-ESPHome.yaml`, 659 lines, pushed 2026-08-04, no license file).
- Upstream issue [ankohanse/hass-dab-pumps#110](https://github.com/ankohanse/hass-dab-pumps/issues/110): maintainer confirms BLE requires a button press on the pump to enter advertising mode; another user states the app uses BLE only for the initial handshake and then an ad-hoc Wi-Fi link.

Protocol as reconstructed from the ESPHome YAML (not independently verified here):

| Item | Value |
| --- | --- |
| GATT service | `FF01` |
| Request characteristic | `FF01` (write; JSON `{"p": "<path>", "m": "GET"}`) |
| Response characteristic | `FF02` (notify; chunked JSON `{"k": <req id>, "s": <seq>, "b": "<fragment>"}` terminated by `{"k": <req id>, "c": true}`) |
| Endpoints seen | `/api/v1/getmainserial`, `/api/v1/configure/settings`, `/api/v1/getDUMInfo/<serial>`, `/api/v1/configure/paramsDefinitionCompressed?doc=<style>&version=0` (gzip+base64 parameter definitions, needs `/api/v1/ack` after receipt), `/api/v1/configure/getparams/<serial>` (polled every ~8 s), `/api/v1/services` (Wi-Fi scan) |
| Parameter keys seen | `VP_PressureBar`, `SP_SetpointPressureBar`, `RP_PressureFallToRestartBar`, `RM_MaximumSpeed`, `PumpStatus`, `SystemStatus`, `PowerShowerBoost`, `SleepModeEnable`, `TE_HeatsinkTemperatureC`, `PO_OutputPower`, `C1_PumpPhaseCurrent`, `SV_SupplyVoltage`, `LvVersion`, `HvVersion`, `Error1..N` — the same key namespace the cloud API returns, so `data.py`'s `PARAM_INFOS` table carries over |
| Writes (POST) | Not yet reverse-engineered by anyone as of 2026-08-28 (last post in the thread) |

Known limitations reported:

- The pump only advertises after a physical button press; the link drops after ~20 min of inactivity and the pump reverts to Wi-Fi/cloud mode. The ESPHome PoC keeps the link alive by polling every 8 s. Whether a power cycle re-enters advertising mode is untested.
- Sequence counters (`k`) grow; a periodic reconnect may be required.
- The pump's own Wi-Fi interface is "pretty much locked down if not connected to the cloud" (Spaco). No local HTTP endpoint has been found on the LAN side.

### B. Modbus RTU on DConnect Box / Box 2 (older Esybox, Esybox Diver, EvoPlus, MCE-P, Active Driver, e.Box)

Evidence: DAB's own document *DConnect BOX KIT Modbus* (60198769, 2024-06,
[PDF](https://www.dabpumps.es/sites/dabpumps.es/files/2024-06/60198769_DCONNECT-MODBUS.pdf)).

- Requires accessory **60198693 — KIT adattatore DConnect BMS Modbus RTU RS485** plugged into the DConnect Box, plus a one-time firmware update of the Box over the internet.
- The Box becomes a single Modbus RTU **slave** (address 1–247, 4800/9600/19200 8N1) exposing every connected pump. Function codes 0x03 (read holding), 0x06 (write single), 0x10 (write multiple).
- Register layout: `family offset + position offset (0x0100 per pump) + parameter offset`. Family offsets: Active Driver Plus `0x0000`, ADAC `0x1000`, DConnect Box `0x2000`, e.Box `0x3000`, **e.Syline (Esybox) `0x4000`**, MCE-P `0x5000`. Serial number: 25 registers from offset 0 of the family block.
- Parameter offsets relevant to control (e.Syline column marked supported): `0x1A` product type, `0x1C` setpoint pressure bar (R/W, ×0.1), `0x1E` restart pressure bar (R/W, ×0.1), `0x20` pressure bar (×0.1), `0x23` pump status enum, `0x24` system status, `0x25` latest error, `0x3A/0x3C/0x3E` aux setpoints.
- HA can consume this directly with the core `modbus` integration via an RS485-to-Ethernet or RS485-USB adapter; no custom code needed for the read/write register path.

Unknown: whether the DConnect Box keeps serving Modbus with cloud access blocked, or
whether it eventually refuses (licence check, time sync). Must be tested before firewalling it.

## Recommended path

1. **Identify the pump** (model label / `ProductType`). No DAB device is configured in the live HA (`core.config_entries` on BowmanMtn has no `dabpumps` entry as of 2026-10-14), so this cannot be read from HA state.
2. **If it is an H2D pump with built-in BLE** (Esybox Mini 3 / Esybox Max / new Esybox / Esybox Pop):
   - Flash the `DAB-ESPHome` YAML to an ESP32 near the pump, press the pump's local-connection button, confirm `getparams` data arrives. This is the cheapest end-to-end proof.
   - Capture the H2D app's BLE writes for setpoint changes (nRF Sniffer workflow in `../mitm-de-cloud/_shared/tools/ble-sniff.md`) to learn the POST format. That is the missing piece for control.
   - Then either keep ESPHome as the bridge (entities arrive via the ESPHome integration; no changes to this repo), or implement a `bleak`-based transport in this integration. The integration already isolates the cloud behind `DabPumpsApiWrap` (`api.py`); the coordinator only consumes `device_map`, `device_config_map`, `device_state_map` and calls `async_change_device_status`. A local backend has to populate those three maps from `getparams` + the gzip parameter definition, and the existing entity/platform code is reusable unchanged.
   - Confirm the firewall rule: block the pump's MAC from WAN; verify the BLE link survives and that the pump does not brick or nag.
3. **If it is an older pump behind a DConnect Box/Box 2**: buy the 60198693 RS485 kit, do the one-time firmware update, then move the Box to a no-WAN VLAN and drive it with HA's `modbus` integration. Verify the Box still answers 0x03 reads after 24 h without internet before relying on it.
4. **Either way, the cloud account can then be deleted.** This repo stays useful only as the parameter-metadata table (`data.py`) and entity mapping (`helper.py`).

## Verification commands for whoever picks this up

```bash
# Is DAB traffic leaving the house right now? (run on the gateway / capture VLAN)
tshark -i <iface> -Y 'tls.handshake.extensions_server_name contains "dabpumps" or tls.handshake.extensions_server_name contains "dabcs"'

# Is the pump advertising over BLE after the button press?
# (macOS) use nRF Connect / LightBlue and look for service FF01

# Modbus path: read product type of the first e.Syline device via a serial adapter
mbpoll -a <box-address> -b 9600 -P none -t 4:hex -r $((0x4000 + 0x1A + 1)) -c 1 /dev/ttyUSB0
```
