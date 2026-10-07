# Root-cause notes — why the first attempts failed

Two real bugs found in `mtkclient`'s BROM flow on MT6768. Both are worth knowing before you retry.

## 1. The BROM watchdog gives you ~2.7 s

`dmesg` on the host shows the whole story:

```
91211.025  usb 3-1: New USB device found, idVendor=0e8d, idProduct=2000   <- BROM appears
91211.129  cdc_acm 3-1:1.0: ttyACM0: USB ACM device
91213.697  usb 3-1: USB disconnect, device number 45                      <- gone, 2.67 s later
91233.880  usb 3-1: New USB device found, idVendor=0e8d, idProduct=2046   <- booted Android instead
```

So: **BROM only stays enumerated for ~2.7 s** unless something talks to it first. `mtkclient`
must already be in its poll loop when the port appears. Starting it *after* you plug in always
loses the race. Its own hint text says exactly this:

```
Preloader - Status: Waiting for PreLoader VCOM, please reconnect mobile/iot device to brom mode
```

That message is an **instruction**, not an error. Launch it, *then* do the button dance.

## 2. `dumppreloader` wedges the port permanently

`dumppreloader` disables the BROM watchdog (`Preloader - Disabling Watchdog...`) in order to dump
safely. Correct for that run — but it means the port **never auto-resets**. It stays as
`0e8d:0003` with a handshake that always fails.

Consequence — and this is the trap:

`mtkclient/Library/DA/mtk_da_handler.py` `connect()`:

```python
mtk.port.cdc.connected = mtk.port.cdc.connect()
if mtk.port.cdc.connected is None or not mtk.port.cdc.connected or mtk.serialportname is not None:
    mtk.preloader.init(directory=directory)
    ...
else:
    if mtk.port.cdc.connected and os.path.exists(os.path.join(mtk.config.hwparam_path, ".state")):
        ...
if mtk.config.target_config is None:
    self.info("Please disconnect, start mtkclient and reconnect.")
    return None          # <- aborts here, no handshake attempted
```

A leftover `0e8d:0003` from the previous session makes `connect()` succeed. That takes the `else`
branch, **skips `preloader.init()` entirely**, leaves `target_config = None`, and the run dies with
*"Please disconnect, start mtkclient and reconnect."* — which reads like a handshake failure but
is really "I thought I was already connected."

**Fixes:**
- Pass `--preloader <file>` and never call `dumppreloader` again in the same session.
- Clear the stale device before retrying:
  ```bash
  for d in /sys/bus/usb/devices/*/idVendor; do
    [ "$(cat $d)" = "0e8d" ] && NODE=$(basename $(dirname $d))
  done
  echo -n "$NODE" | sudo tee /sys/bus/usb/drivers/usb/unbind
  ```
- Or simply power-cycle the tablet.

## 3. Why DSU was impossible here

`com.android.dynsystem` on this build only binds:

```
install, isInstalled, setEnable, remove, getActiveDsuSlot, getInstallationProgress
```

There is **no `install_boot` and no `get_boot_control`**. The DSU `Restart` action calls
`powerManager.reboot("dynsystem")`, and LK ignores it — the device comes straight back to stock
(`slot _a`, stock fingerprint, `adb root` still refused). A normal boot also *removes* the staged
install. So the DSU/GSI-bootstrap route in the community guide (XDA 4793639) does not apply to this
bootloader; BROM is the only path.

Confirming evidence after the attempt:
- DSU notification reposted fresh (`NOTIFY_IF_IN_USE`, 2 actions) → system knew it was only *staged*
- no `ro.boot.dsu.*`, no `dynsystem` in `ro.boot.mode`, `gsid=stopped`

## 4. `cdc_acm` interferes

BROM enumerates a CDC-ACM interface. The kernel binds `cdc_acm` and creates `/dev/ttyACM0`, which
competes with libusb for the interface. Unload it for clean access:

```bash
sudo modprobe -r cdc_acm
```

`0e8d` also means the USB device node (`3-1` here) is not always released promptly after a session,
which is why unbinding by sysfs node is more reliable than `usbreset`.
