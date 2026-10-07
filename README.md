# onn 7" Core (2026) tablet — rooting it, and backing it up first

**What this is:** a step-by-step kit for rooting *one specific cheap Walmart tablet* and
making a complete, verified, restore-able copy of it before you change anything.

**Who it's for:** someone who owns this exact tablet, is comfortable in a Linux terminal,
and wants root access. If you've never used a terminal, this isn't the place to start.

---

## Start here — the 30-second version

**What "rooting" means:** Android normally hides the parts of the system that
control the tablet. Rooting removes that restriction so you can change anything.
It's your tablet, so that's a reasonable thing to want.

**What "backup" means here:** this tablet's manufacturer **never published the
firmware**. There is no download to recover from. So before touching anything,
you copy the tablet's entire internal storage to your computer. That copy is
the **only** one that will ever exist. If you skip this and something breaks,
the tablet is a paperweight.

**The order matters, and it is not negotiable:**

```
1. Copy the tablet   ->  2. Verify the copy   ->  3. ONLY THEN root it
```

Most guides tell you to root first. On this device that's how people brick
their tablets, because there is no firmware to reinstall afterwards.

**Time:** about 45 minutes, most of it waiting for the copy.
**Risk:** moderate. Read the warnings before each step — they're there because
someone hit that exact problem.

---

## Does this work on your tablet?

**It must be the same tablet AND the same software build.** Same model number
is *not* enough — the factory shipped several builds and they are not
interchangeable. Flashing the wrong one bricks the device.

| | |
|---|---|
| **Tablet** | onn 7" Core, 2026, sold at Walmart |
| **Model number** | `36020962` |
| **Internal name** | `onn7core` |
| **Board** | `mid7030_mx_64` |
| **Chip inside** | MediaTek MT8786V/N (`mt6768`, aka Helio P65 / G85) |
| **Android** | 16, build `BP2A.250605.031.A3`, built 2026-01-21 |
| **Result** | Rooted with Magisk 30.7, survives reboot |

**Run this first — it checks for you, and only reads, changes nothing:**

```bash
./scripts/preflight.sh
```

It must print `PREFLIGHT PASSED`. If it doesn't, **stop** — the rest of this
guide does not apply to your tablet.

---

## What you need

- This exact tablet, and the ability to follow instructions carefully
- A computer running **Linux** (this was built and tested on Linux Mint)
- A **spare USB port, ideally USB 2.0.** Not a hub. Not USB 3 — the chip's
  recovery mode is unreliable there.
- A **USB-C cable that carries data.** Many cheap cables only carry power and
  will silently waste an hour of your time.
- **About 11 GB of free disk space** for the copy
- Tablet **charged past 50%**

---

## The steps

### 1. Install the tools

```bash
./scripts/setup.sh
```

Downloads the Android tools, the MediaTek unlock utility (`mtkclient`, pinned
to an exact version), the Python packages it needs, the rules that let you talk
to the chip without `sudo`, and Magisk 30.7 (checked against its published hash).
Safe to run twice.

### 2. Check the tablet matches

```bash
./scripts/preflight.sh
```

Read-only. Compares your tablet against the known-good one and checks free
space and tool versions. **Only continue if it says `PREFLIGHT PASSED`.**

### 3. Unlock the bootloader — *only if it's still locked*

⚠️ **This erases the tablet.** Everything on it. Do this before you put
anything personal on it, or back up your files separately first.

```bash
adb reboot bootloader
fastboot flashing unlock        # confirm using the tablet's volume keys
```

Check it worked: `fastboot getvar unlocked` should say `yes`. (The tablet used
to write this guide was already unlocked when the work started, so if yours is
too, skip this step.)

### 4. Make the backup — the part that can't be redone

⚠️ **This is the important step. If you rush it, you can't undo it.**

The tablet will be in a special low-level mode called **BROM** (the chip's
built-in recovery mode). The tricky part: it only stays available for about
**2.7 seconds** after you plug in, then it gives up and boots Android instead.

**So you start the tool FIRST, and plug the cable in AFTER.** Other way round
always fails. This is the single biggest gotcha.

**4a. Start the copy tool** (it will just sit there waiting — that's correct):

```bash
./scripts/read_all.sh
```

**4b. Now put the tablet into BROM mode:**

1. Hold **Power for ~12 seconds** → screen goes fully black and stays black
2. Wait 5 seconds
3. Hold **Volume Up + Volume Down together**
4. **While still holding both, plug the USB cable in**
5. Keep holding for 8 seconds, then let go

The waiting tool should catch it and print something like:

```
Preloader - CPU:     MT6768/MT6769(Helio P65/G85 k68v1)
Preloader - HW code: 0x707
```

**4c. What it copies:** the whole boot chain plus a 9.7 GB system partition.
At ~11 MB/s the boot parts take ~2 minutes and the big one about 15 minutes.

**It will print a warning saying some partitions failed. That's expected** — the
two "preloader" entries aren't normal partitions and can't be read that way.
Ignore that specific message.

**4d. Verify the copy. Do this before anything else.**

```bash
./scripts/verify_backup.sh
```

Must end with `RESULT: ALL CHECKS PASSED`. **Do not flash anything until it does.**

### 5. Prepare the rooted boot file

```bash
adb install -r Magisk-v30.7.apk
adb push backup/init_boot_a.img /sdcard/Download/
```

Then **on the tablet**, open Magisk and choose:
**Install → "Select and patch a file" → `init_boot_a.img`**

Use *that* option specifically, not the "recommended" one it suggests. It
creates a new file, and you copy it back:

```bash
adb pull /sdcard/Download/magisk_patched-30700_XXXXX.img .
```

### 6. Flash it and check root works

```bash
./scripts/flash_magisk.sh magisk_patched-30700_XXXXX.img
```

The script checks the file, confirms the bootloader is unlocked, works out which
slot is active, flashes it, reboots, and checks the result.

You want to see:

```
uid=0(root) gid=0(root) groups=0(root) context=u:r:magisk:s0
30.7:MAGISK:R
```

The first time an app asks for root it gets **denied by default** — that's normal.
Open **Magisk → Superuser**, find the entry, and switch it on. Then reboot to
confirm root survives.

### 7. If something goes wrong

```bash
./scripts/restore_stock.sh
```

Puts the stock system back. This works **because** you unlocked the bootloader
and the chip's protection is off — and because you made the backup in step 4.
If you skipped the backup, this step cannot save you.

---

## Technical reference

<details>
<summary><b>Click to expand — partition table, pitfalls, and internals</b></summary>

### Partition reference (verified on slot `a`)

| Partition | Bytes | Notes |
|---|---:|---|
| `preloader_a/b` | 330,216 | raw eMMC boot region, **not a GPT partition**; appears as `preloader_raw_a/b` in fastboot |
| `boot_a` | 67,108,864 | `ANDROID!` |
| `init_boot_a` | 8,388,608 | `ANDROID!` — **this is what Magisk patches** |
| `vendor_boot_a` | 67,108,864 | `VNDRBOOT`; contains the stock recovery ramdisk |
| `dtbo_a` | 8,388,608 | |
| `boot_para` | 27,262,976 | `para` in fastboot |
| `vbmeta_a` | 8,388,608 | `AVB0` |
| `vbmeta_system_a` / `vbmeta_vendor_a` | 8,388,608 | |
| `lk_a` | 2,097,152 | bootloader (little kernel) |
| `md1img_a` | 134,217,728 | modem |
| `tee_a` | 7,340,032 | |
| `scp_a` | 12,582,912 | |
| `sspm_a` | 2,097,152 | |
| `spmfw_a` | 1,048,576 | |
| `gz_a` | 33,554,432 | |
| `super` | 9,663,676,416 | single, **not slotted**; system + product + vendor (EROFS) |

### The one binary that ships with this repo

`preloader/preloader_mid7030_mx_64.bin` (330,216 bytes, `sha256 25468f7c…`) is the
DRAM setup for this exact chip. `mtkclient` cannot talk to BROM without one, and it's
specific to the device — it was pulled out of this unit's own memory. It's small, so
it's included to make the process reproducible without an awkward chicken-and-egg
first run.

**Caveat, stated plainly:** preloaders are *usually* identical across units of the same
build, but they can differ by production batch. The scripts don't depend on the shipped
one being byte-perfect — if a read or write misbehaves, dump your own (step 4b) and
`read_all.sh` will use the one you put in `preloader/`.

### Pitfalls

Full analysis in `ROOT-CAUSE-NOTES.md`. The short list:

- **2.7-second BROM window.** Arm the tool first, then plug in. Always.
- **`dumppreloader` wedges the port** — it disables the watchdog, so the chip never
  auto-resets. Power-cycle the tablet afterwards.
- **Stale-port trap:** a leftover `0e8d:0003` device makes mtkclient skip its handshake
  and abort. Fix with `./scripts/clear_stale_port.sh`.
- **`cdc_acm` fights libusb:** `sudo modprobe -r cdc_acm`.
- **`fastboot fetch` doesn't work** on this device — you cannot read stock images over
  fastboot, only over BROM.
- **DSU is impossible here.** `com.android.dynsystem` is missing `install_boot` and
  `get_boot_control`, so `reboot("dynsystem")` is ignored by the bootloader. The GSI/DSU
  route in other community guides does not apply.
- **No official firmware exists, ever.** Your dump is the only copy in existence.
- **Never flash another unit's build.** Same model number ≠ same software build.

### Files in this repo

```
README.md                      this file
config/manifest.yaml           every pinned version: device, toolchain, artifacts
config/device-fingerprint.txt  verbatim getprop + partition map from the reference unit
preloader/                     the preloader binary — the one thing that can't be re-derived
requirements-lock.txt          pinned python deps for mtkclient
SHA256SUMS                     hashes of the reference 17-partition backup set
ROOT-CAUSE-NOTES.md            the two mtkclient bugs + why DSU cannot work here
scripts/setup.sh               build the exact toolchain (safe to re-run)
scripts/preflight.sh           read-only: verify the tablet matches before touching it
scripts/read_all.sh            one-shot BROM read (boot chain + super)
scripts/verify_backup.sh       sizes, magics, zero-byte check, write SHA256SUMS
scripts/flash_magisk.sh        validate + flash patched init_boot, verify root
scripts/restore_stock.sh       restore stock (fastboot, then BROM)
scripts/clear_stale_port.sh    unwedge a stale BROM port
scripts/privacy_check.sh       assert no personal identifiers are present
```

`backup/` is **not** committed — it's 9.4 GB of device-specific images. `SHA256SUMS`
records what a correct copy must hash to.

</details>

---

## Licensing and credits

- **Magisk** — GPL-3.0, by topjohnwu. Downloaded, not bundled.
- **mtkclient** — by bkerler. Cloned at a pinned commit, not bundled.
- **Vector** (LSPosed fork, GPL-3.0) — by JingMatrix. **Not bundled**; get it from
  its upstream releases.
- The scripts and notes here are provided as-is, with no warranty. Rooting voids
  warranties and can destroy your device if you deviate from these steps.
