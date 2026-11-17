# Home Network Manager for Home Assistant

Every computer that the network manager tracks,
as a Home Assistant device with a sensor per value: state, CPU, memory, disks, GPUs, temperatures, logged-in users,
addresses, pending updates, reboot required, and more.

Home Assistant connects out to the network manager with a key; the network manager pushes the full list over that
WebSocket on connect and then at the push interval set under Admin → Settings (`local_push`, no polling).

## Install

1. HACS → ⋮ → Custom repositories → add this repository as **Integration** → download **Home Network Manager** →
   restart Home Assistant.
2. In Home Network Manager, open **Admin → Settings → Home Assistant** and select **Create key**. The key is shown
   once.
3. In Home Assistant, **Settings → Devices & services → Add integration → Home Network Manager**, then enter the
   server URL (the address you open the network manager at) and the key.

Replacing or removing the key in the network manager disconnects Home Assistant at once; it then asks for the new key
under Settings → Devices & services.

## What you get

- One device per computer, under the server's own device. LXC containers are left out: only actual devices show.
- A sensor per data point, named by the server. A value the server doesn't have right now (CPU of a computer that is
  off, say) shows unavailable. New data points appear on the next push.
- On the server device: **Connected** and **Last update**.
- A computer deleted in the network manager is removed from Home Assistant on the next push.

The integration ships its own icon (`custom_components/home_network_manager/brand/`), which Home Assistant 2026.10
and newer shows on its integration and device pages.

Diagnostics (Settings → Devices & services → ⋮ → Download diagnostics) include the last snapshot, with the key
redacted.

## Development

```sh
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff format --check custom_components tests && .venv/bin/ruff check custom_components tests
.venv/bin/mypy custom_components && .venv/bin/pytest tests
```

This repository is published automatically from the network manager's own repository; changes made here are
overwritten by the next release.
