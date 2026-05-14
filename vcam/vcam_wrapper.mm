// vcam_wrapper.mm — C API around OBSDALMachServer for Python ctypes
// Pushes BGRA frames to the OBS Virtual Camera DAL plugin via Mach ports.

#import <Foundation/Foundation.h>
#import <CoreVideo/CoreVideo.h>
#import <mach/mach_time.h>
#import "OBSDALMachServer.h"

// ── State ──────────────────────────────────────────────────────
static OBSDALMachServer   *g_server     = nil;
static CVPixelBufferPoolRef g_pool       = nil;
static uint32_t             g_width      = 0;
static uint32_t             g_height     = 0;
static uint32_t             g_fps_num    = 30000;
static uint32_t             g_fps_den    = 1000;
static uint32_t             g_seed       = 0;
static mach_timebase_info_data_t g_timebase;

// Convert mach_absolute_time to nanoseconds (what the DAL plugin timestamp expects)
static uint64_t mach_time_ns() {
    uint64_t t = mach_absolute_time();
    // scale: t * numer / denom
    uint64_t hi  = (t >> 32) * g_timebase.numer;
    uint64_t lo  = (t & 0xffffffff) * g_timebase.numer / g_timebase.denom;
    uint64_t hiR = ((hi % g_timebase.denom) << 32) / g_timebase.denom;
    return ((hi / g_timebase.denom) << 32) + hiR + lo;
}

// ── Public C API (called from Python via ctypes) ───────────────

extern "C" {

int vcam_start(uint32_t width, uint32_t height, uint32_t fps) {
    if (g_server != nil) return 0; // already running

    g_width   = width;
    g_height  = height;
    g_fps_num = fps * 1000;
    g_fps_den = 1000;
    mach_timebase_info(&g_timebase);

    // Create an IOSurface-backed BGRA pixel buffer pool
    NSDictionary *pbAttrs = @{
        (id)kCVPixelBufferPixelFormatTypeKey:     @(kCVPixelFormatType_32BGRA),
        (id)kCVPixelBufferWidthKey:               @(width),
        (id)kCVPixelBufferHeightKey:              @(height),
        (id)kCVPixelBufferIOSurfacePropertiesKey: @{},
    };
    CVReturn st = CVPixelBufferPoolCreate(kCFAllocatorDefault, nil,
        (__bridge CFDictionaryRef)pbAttrs, &g_pool);
    if (st != kCVReturnSuccess) {
        fprintf(stderr, "[vcam] CVPixelBufferPoolCreate failed (%d)\n", st);
        return -1;
    }

    g_server = [[OBSDALMachServer alloc] init];
    if (![g_server run]) {
        fprintf(stderr, "[vcam] OBSDALMachServer run failed — is another instance running?\n");
        g_server = nil;
        CVPixelBufferPoolRelease(g_pool);
        g_pool = nil;
        return -2;
    }

    fprintf(stderr, "[vcam] Started (%ux%u @ %u fps)\n", width, height, fps);
    return 0;
}

// frame_bgra: pointer to raw BGRA pixels (width * height * 4 bytes)
int vcam_send_frame(const uint8_t *frame_bgra) {
    if (!g_server || !g_pool) return -1;

    // Drain the RunLoop once so the Mach server can process client connect messages
    [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode
                             beforeDate:[NSDate date]];

    CVPixelBufferRef pb = nil;
    CVReturn st = CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, g_pool, &pb);
    if (st != kCVReturnSuccess || !pb) {
        fprintf(stderr, "[vcam] CVPixelBufferPoolCreatePixelBuffer failed (%d)\n", st);
        return -2;
    }

    CVPixelBufferLockBaseAddress(pb, 0);
    uint8_t *dst      = (uint8_t *)CVPixelBufferGetBaseAddress(pb);
    size_t   dst_size = CVPixelBufferGetDataSize(pb);
    size_t   src_size = (size_t)g_width * g_height * 4;
    memcpy(dst, frame_bgra, src_size < dst_size ? src_size : dst_size);
    CVPixelBufferUnlockBaseAddress(pb, 0);

    uint64_t ts = mach_time_ns();
    [g_server sendPixelBuffer:pb
                    timestamp:ts
                 fpsNumerator:g_fps_num
               fpsDenominator:g_fps_den];

    CVPixelBufferRelease(pb);
    return 0;
}

void vcam_stop() {
    if (g_server) {
        [g_server stop];
        g_server = nil;
    }
    if (g_pool) {
        CVPixelBufferPoolRelease(g_pool);
        g_pool = nil;
    }
    fprintf(stderr, "[vcam] Stopped\n");
}

} // extern "C"
