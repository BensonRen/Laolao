# WS-H — camera acquisition reliability on the Electron path

Machine: Snapdragon X2 Elite, Windows 11 ARM64. Camera: **ASUS FHD webcam** (external USB),
not the internal Qualcomm Spectra ISP device. Completed by the orchestrator after the
original WS-H agent was stopped mid-measurement.

## The reported defect

Native ARM64 Electron composites the webcam plus captions and streams them to the virtual
camera. It was observed working, then on a later run the output window hung inside camera
startup and produced **zero** frames. The diagnostic log simply stopped:

```
[OUTPUT ] startCamera: device=a3717bbc… format=720p30
captureLoop: started
[CONTROL] preview: STALE — no output frames for >3s (received 0 total)
```

No "video playing", no error, no exception, for the remaining ~99 seconds.
`getUserMedia` was **hanging, not rejecting**.

WS-H's last message before it was stopped claimed the baseline "wedges on essentially
every run". **That claim does not survive measurement** — see below. It was almost
certainly self-inflicted: relaunching while its own previous instance still held the
camera.

## Measurement — three consecutive cold launches

Clean kill of `electron` **and** the Laolao python children, then a 4 s gap, then relaunch:

| run | getUserMedia | video playing | frames in 20 s |
|---|---|---|---|
| 1 | 669 ms | yes | 245 |
| 2 | 648 ms | yes | 245 |
| 3 | 655 ms | yes | 240 |

**3/3 succeeded.** The camera is not broadly unreliable. It wedges specifically when the
device is still held — by a prior Laolao instance, by OBS, or by a browser tab — because
Windows releases a USB webcam lazily.

## Root cause

`getUserMedia` against a busy device does not reject; it waits. The code had no timeout,
so a busy camera produced an unbounded black screen with nothing in the log after
`startCamera:`. For this product that is the worst possible failure framing — the user
concludes the app is broken rather than that a camera is in use.

## Fix

`startCamera()` now races a **12 s timeout** (`getUserMediaOrTimeout`). On a
`CameraBusyTimeout` against an *exact* `deviceId` it retries once **without** the id,
which covers both "this camera is busy" and "the saved id went stale after a replug",
then falls through to the existing format ladder. `deviceId` is null on the retry, so it
cannot recurse more than once.

### Verified deterministically

The timeout budget was temporarily dropped to 100 ms — below the ~640 ms the camera
genuinely needs — so the path had to fire:

```
FAILED after 104ms CameraBusyTimeout: camera did not respond within 100ms
retrying with the default camera instead of the saved one
device=(default) 720p30 → 720p15 → 480p30 → default, then gave up cleanly
```

Bounded, logged, self-explaining, terminating. Restored to 12000 ms; normal startup
re-verified at **637 ms**, `video playing 1280x720`, first non-black frame at #8.

## Still open

- **~12 fps, not 30.** The capture loop targets 30 fps and sustains about 12 (245 frames
  / 20 s). Almost certainly the Adreno X2-90 GPU-process crash (`exit_code=34`) forcing
  Chromium onto software rendering. Not addressed. This is the main quality gap left on
  the Electron path — the far end sees noticeably choppy video.
- **Only one program can hold the webcam.** The timeout makes contention *survivable*, not
  absent. The launcher still has to guarantee a single consumer, and OBS-as-compositor
  remains the shipping default.

## Recommendation

The Electron path is now materially safer to run — a busy camera degrades to a logged,
recoverable failure instead of a silent black feed. It is **still not the default**,
because of the 12 fps software-rendering gap rather than the acquisition bug that
prompted this investigation. Fixing the GPU crash is the prerequisite for promoting it.

## Note on process

The original agent reported a reproduction rate ("essentially every run") that was an
artifact of its own test harness holding the device between attempts. The correction cost
one controlled experiment. Worth remembering that a flaky-bug reproduction rate is itself
a measurement, and needs the same scepticism as any other.
