# OBS Virtual Camera DAL Plugin

The Electron app needs the OBS Virtual Camera DAL plugin to register as a system camera.

## Where to get it

**Option A — Extract from OBS (recommended):**
1. Download OBS Studio from https://obsproject.com/
2. Open the `.dmg`, right-click `OBS.app` → Show Package Contents
3. Copy `Contents/Resources/obs-plugins/obs-mac-virtualcam.plugin` to this directory

**Option B — Already have OBS installed:**
The plugin is already at `/Library/CoreMediaIO/Plug-Ins/DAL/obs-mac-virtualcam.plugin`.
The Electron app detects this automatically — no copy needed.

## Licensing

The OBS Virtual Camera plugin is licensed under the GNU General Public License v2.
Source: https://github.com/obsproject/obs-studio

By bundling this plugin, the Laolao Electron app must also be distributed under GPLv2
(or a compatible license). See the OBS repository for full license text.

## File to place here

```
electron/resources/obs-mac-virtualcam.plugin/   ← directory (it's a macOS bundle)
```
